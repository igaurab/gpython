"""Import name → PyPI name, and reading the module out of a ModuleNotFoundError (no kernel)."""

from gpython import packages


def err(ename: str, evalue: str) -> dict:
    return {"ename": ename, "evalue": evalue, "traceback": []}


def test_missing_module_reads_module_not_found_errors_only():
    assert packages.missing_module(err("ModuleNotFoundError", "No module named 'numpy'")) == "numpy"
    assert packages.missing_module(err("ModuleNotFoundError", "No module named 'google.cloud.storage'")) == "google.cloud.storage"
    assert packages.missing_module(err("ModuleNotFoundError", "No module named 'foo.bar'; 'foo' is not a package")) == "foo.bar"
    assert packages.missing_module(err("ImportError", "cannot import name 'x' from 'numpy'")) is None
    assert packages.missing_module(err("ZeroDivisionError", "division by zero")) is None
    assert packages.missing_module({"traceback": ["Kernel died"]}) is None
    assert packages.missing_module(None) is None


def test_stdlib_modules_are_not_packages():
    assert packages.is_stdlib("tkinter") and packages.is_stdlib("_tkinter") and packages.is_stdlib("sqlite3")
    assert not packages.is_stdlib("numpy") and not packages.is_stdlib("yaml")


def test_pypi_name_maps_renamed_packages_and_namespaces():
    assert packages.pypi_name("numpy") == "numpy"
    assert packages.pypi_name("cv2") == "opencv-python"
    assert packages.pypi_name("sklearn.linear_model") == "scikit-learn"
    assert packages.pypi_name("PIL.Image") == "pillow"
    assert packages.pypi_name("yaml") == "pyyaml"
    assert packages.pypi_name("bs4") == "beautifulsoup4"
    assert packages.pypi_name("google.protobuf") == "protobuf"
    assert packages.pypi_name("google.cloud.storage") == "google-cloud-storage"
    assert packages.pypi_name("azure.storage.blob") == "azure-storage-blob"
    assert packages.pypi_name("azure.identity") == "azure-identity"
    assert packages.pypi_name("sentence_transformers") == "sentence_transformers"


def test_pypi_name_uses_the_cell_imports_for_namespace_roots():
    source = "from google.cloud import storage\nimport os\n"
    assert packages.pypi_name("google", source) == "google-cloud-storage"
    assert packages.pypi_name("google", "import google.protobuf as pb") == "protobuf"
    assert packages.pypi_name("azure", "from azure.identity import DefaultAzureCredential") == "azure-identity"
    assert packages.pypi_name("numpy", "import numpy as np") == "numpy"
    assert packages.pypi_name("google", "this is not python (") == "google"


def test_the_table_is_packages_json_next_to_the_module():
    table = packages.pypi_names()
    assert table["cv2"] == "opencv-python" and table["google.protobuf"] == "protobuf"
    assert list(table) == sorted(table, key=str.lower)
