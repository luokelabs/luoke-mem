"""Imported first by every test module: whatever the tests do stays in a temporary place, in whatever order the
modules load or however few of them run. From here on the library
refuses the real memory (~/.luoke) and the real or lab hub, in this process and the ones it starts."""
import os
import tempfile

HOME = tempfile.mkdtemp(prefix="luoke-test-home-")
os.environ["LUOKE_MEM_TEST"] = "1"
os.environ["LUOKE_HOME"] = HOME
os.environ.pop("LUOKE_HUB_SOCKET", None)
