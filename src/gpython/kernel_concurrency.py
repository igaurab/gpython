"""Runs a group of independent cells at the same time, inside the kernel.

gpython reads this file and executes it in the kernel as the module `_gpython_concurrency` the first
time a run has a group of more than one cell (see `_gp_run` in kernel.py), so it uses the standard
library only. Which cells may share a group is decided in gpython (concurrency.py); this module only
runs them: one thread per cell in the shared namespace, each cell's output routed to it with a
`switch` marker in the stdout marker protocol, an interrupt delivered to every thread, BLAS held to
its share of the cores, and the shared names it was told to watch checked for changes afterwards.
"""

from __future__ import annotations

import ast
import contextvars
import ctypes
import os
import signal
import sys
import threading
import zlib
from concurrent.futures import ThreadPoolExecutor, wait

CELL: contextvars.ContextVar[str | None] = contextvars.ContextVar("gpython_cell", default=None)
BUFFER_CAP = 16 << 20
ITEMS_CAP = 10_000


class OutputRouter:
    """While a group runs, stdout, stderr and rich displays carry the id of the cell that made them.

    Every write goes through one lock; a writer that is not the cell the stream last spoke for
    first emits a `switch` marker and flushes, so gpython files what follows under that cell."""

    def __init__(self, shell, mark, lock: threading.RLock) -> None:
        self.shell = shell
        self.mark = mark
        self.lock = lock
        self.last: str | None = None
        self.saved: tuple = ()

    def speak_for(self, cid: str | None) -> None:
        if cid is not None and cid != self.last:
            self.saved[1].flush()
            self.mark(cid, "switch")
            self.saved[0].flush()
            self.last = cid

    def __enter__(self) -> OutputRouter:
        publisher = getattr(self.shell, "display_pub", None)
        self.saved = (sys.stdout, sys.stderr, publisher, getattr(publisher, "publish", None))
        sys.stdout = _Stream(self, self.saved[0])
        sys.stderr = _Stream(self, self.saved[1])
        if publisher is not None:
            original = self.saved[3]

            def publish(*args, **kwargs):
                with self.lock:
                    self.speak_for(CELL.get())
                    return original(*args, **kwargs)

            publisher.publish = publish
        return self

    def __exit__(self, *exc) -> None:
        stdout, stderr, publisher, original = self.saved
        sys.stdout, sys.stderr = stdout, stderr
        if publisher is not None:
            publisher.publish = original
        stdout.flush()
        stderr.flush()


class _Stream:
    def __init__(self, router: OutputRouter, real) -> None:
        self.router = router
        self.real = real

    def write(self, text: str) -> int:
        with self.router.lock:
            self.router.speak_for(CELL.get())
            return self.real.write(text)

    def writelines(self, lines) -> None:
        for line in lines:
            self.write(line)

    def flush(self) -> None:
        with self.router.lock:
            self.real.flush()

    def __getattr__(self, name: str):
        return getattr(self.real, name)


class GroupLimits:
    """A pool of one thread per cell for the length of a group, and BLAS held to cores // cells."""

    def __init__(self, size: int) -> None:
        self.size = size
        self.pool: ThreadPoolExecutor | None = None
        self.blas = None

    def __enter__(self) -> ThreadPoolExecutor:
        self.pool = ThreadPoolExecutor(max_workers=self.size, thread_name_prefix="gpython-cell")
        if "numpy" in sys.modules:
            try:
                from threadpoolctl import threadpool_limits

                self.blas = threadpool_limits(max(1, (os.cpu_count() or 1) // self.size))
            except Exception:
                self.blas = None
        return self.pool

    def __exit__(self, *exc) -> None:
        if self.blas is not None:
            self.blas.restore_original_limits()
        self.pool.shutdown(wait=True)


def _split_last(tree: ast.Module) -> tuple[ast.Module, ast.Expression | None]:
    """The cell without its last expression, and that expression (shown like Jupyter's Out)."""
    if tree.body and isinstance(tree.body[-1], ast.Expr):
        last = tree.body[-1].value
        if not (isinstance(last, ast.Constant) and isinstance(last.value, str)):
            return ast.Module(body=tree.body[:-1], type_ignores=[]), ast.Expression(body=last)
    return tree, None


class GroupRunner:
    """Runs the cells of one group on threads and reports each one with begin / end markers."""

    def __init__(self, shell, namespace: dict, mark, lock: threading.RLock) -> None:
        self.shell = shell
        self.namespace = namespace
        self.mark = mark
        self.lock = lock
        self.threads: dict[str, int] = {}
        self.ended: set[str] = set()

    def run(self, cells: list, watch: list[str] | None = None) -> bool:
        """Run `cells`, (cell id, code, names to drop first) each; False if one failed or was interrupted."""
        before = fingerprint(self.namespace, watch) if watch else {}
        prepared = [(cid, self.shell.transform_cell(code), code, names) for cid, code, names in cells]
        router = OutputRouter(self.shell, self.mark, self.lock)
        results: dict[str, bool] = {}
        previous = signal.getsignal(signal.SIGINT)
        with router, GroupLimits(len(cells)) as pool:
            futures = [
                pool.submit(contextvars.copy_context().run, self._one, router, cid, code, raw, names, results)
                for cid, code, raw, names in prepared
            ]
            signal.signal(signal.SIGINT, signal.default_int_handler)
            try:
                pending = set(futures)
                while pending:
                    try:
                        _, pending = wait(pending)
                    except KeyboardInterrupt:
                        self._interrupt()
            finally:
                signal.signal(signal.SIGINT, previous)
        for cid, *_ in cells:
            if cid not in self.ended:
                self._end(router, cid, False)
                results[cid] = False
        if watch:
            changed = sorted(n for n, v in fingerprint(self.namespace, watch).items() if before.get(n) != v)
            changed += sorted(n for n in before if n not in self.namespace)
            if changed:
                self.mark(None, "race", names=changed, cells=[cid for cid, *_ in cells])
        return all(results.get(cid, False) for cid, *_ in cells)

    def _interrupt(self) -> None:
        for ident in list(self.threads.values()):
            ctypes.pythonapi.PyThreadState_SetAsyncExc(ctypes.c_ulong(ident), ctypes.py_object(KeyboardInterrupt))

    def _end(self, router: OutputRouter, cid: str, ok: bool) -> None:
        with self.lock:
            if cid in self.ended:
                return
            self.ended.add(cid)
            router.saved[1].flush()
            self.mark(cid, "end", ok=ok)
            router.last = None

    def _one(self, router: OutputRouter, cid: str, code: str, raw: str, names: list, results: dict) -> None:
        CELL.set(cid)
        self.threads[cid] = threading.get_ident()
        ok = False
        try:
            for name in names:
                self.namespace.pop(name, None)
            with self.lock:
                self.mark(cid, "begin")
                router.last = cid
            filename = self.shell.compile.cache(code, 0, raw)
            body, last = _split_last(ast.parse(code, filename))
            exec(compile(body, filename, "exec"), self.namespace)
            if last is not None and not raw.rstrip().endswith(";"):
                value = eval(compile(last, filename, "eval"), self.namespace)
                if value is not None:
                    data, metadata = self.shell.display_formatter.format(value)
                    self.shell.display_pub.publish(data, metadata)
            ok = True
        except BaseException:
            with self.lock:
                router.speak_for(cid)
                self.shell.showtraceback()
        finally:
            self.threads.pop(cid, None)
            results[cid] = ok
            self._end(router, cid, ok)


def fingerprint(namespace: dict, names) -> dict[str, str]:
    """A cheap identity of each name's value: id, type and length, plus a checksum of an array's
    buffer up to 16 MB or of the ids of a container's items up to 10 000."""
    found = {}
    for name in names:
        if name not in namespace:
            continue
        value = namespace[name]
        parts = [str(id(value)), type(value).__name__]
        try:
            parts.append(str(len(value)))
        except Exception:
            pass
        try:
            if getattr(value, "nbytes", BUFFER_CAP + 1) <= BUFFER_CAP:
                parts.append(str(zlib.crc32(memoryview(value).cast("B"))))
            elif isinstance(value, (list, tuple)) and len(value) <= ITEMS_CAP:
                parts.append(str(zlib.crc32(repr([id(v) for v in value]).encode())))
            elif isinstance(value, dict) and len(value) <= ITEMS_CAP:
                parts.append(str(zlib.crc32(repr([(id(k), id(v)) for k, v in value.items()]).encode())))
            elif isinstance(value, (set, frozenset)) and len(value) <= ITEMS_CAP:
                parts.append(str(zlib.crc32(repr(sorted(id(v) for v in value)).encode())))
        except Exception:
            pass
        found[name] = ":".join(parts)
    return found
