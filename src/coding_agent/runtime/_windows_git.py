"""Read-only Dulwich entry point, run only inside the Windows LPAC.

This module is copied with the shared path policy into the isolated interpreter.
No hooks, subprocess filters, global configuration or repository writes are used.
"""

import json
import sys
from pathlib import Path
from types import SimpleNamespace

if sys.platform != "win32":
    raise ImportError("Windows Git helper")

from dulwich.attrs import GitAttributes
from dulwich.config import ConfigFile, StackedConfig
from dulwich.index import ConflictedIndexEntry, Index
from dulwich.object_store import MemoryObjectStore
from dulwich.objects import Blob
from dulwich.patch import write_object_diff
from dulwich.porcelain import status
from dulwich.repo import Repo

from ..core.paths import path_permitted


class UnsupportedRepository(ValueError):
    """A controller-defined, safe-to-display reason for rejecting repository features."""


class ReadOnlyRepo(Repo):
    def get_config(self) -> ConfigFile:
        with (Path(self.commondir()) / "config").open("rb") as stream:
            config = ConfigFile.from_file(stream, expand_includes=False)
        for section in config.sections():
            if section[0].lower() in {b"filter", b"include", b"includeif"}:
                raise UnsupportedRepository(
                    "Git filters and configuration includes are unsupported"
                )
        # Preserve repository format and native line-ending rules, but prevent
        # user configuration from directing IO or limiting completeness.
        if config.get_boolean(b"core", b"filemode", False):
            raise UnsupportedRepository("POSIX executable-bit tracking is unsupported on Windows")
        safe = ConfigFile()
        for section in config.sections():
            for key, value in config.items(section):
                if section[0].lower() == b"extensions" or (
                    section[0].lower() == b"core"
                    and key.lower()
                    in {
                        b"repositoryformatversion",
                        b"autocrlf",
                        b"eol",
                        b"safecrlf",
                    }
                ):
                    safe.set(section, key, value)
        safe.set(b"core", b"filemode", False)
        safe.set(b"core", b"preloadindex", False)
        return safe

    def get_gitattributes(self, tree: bytes | None = None) -> GitAttributes:
        # Dulwich does not yet implement all nested/working-tree Git attribute
        # semantics. Reject these repositories instead of reporting an incomplete diff.
        attributes = super().get_gitattributes(tree)
        if len(attributes):
            raise UnsupportedRepository("Git attributes are unsupported by the Windows reader")
        return attributes

    def get_config_stack(self) -> StackedConfig:
        return StackedConfig([self.get_config()])

    def open_index(self, config: object = None) -> Index:
        # Index treats a missing file as an empty index, including newly init'ed repositories.
        return Index(self.index_path())


def main() -> None:
    request = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    root = Path(request["root"])
    scope = SimpleNamespace(forbidden=tuple(request["forbidden"]), allowed=())
    operation = request["operation"]
    if any(
        (root / ".git" / name).exists()
        for name in (
            "commondir",
            "objects/info/alternates",
            "info/attributes",
        )
    ):
        raise UnsupportedRepository("external Git metadata or attributes are unsupported")
    with ReadOnlyRepo(
        root, controldir=root / ".git", commondir=root / ".git", worktree=root
    ) as repo:
        index = repo.open_index()
        if operation == "ls-files":
            for path in sorted(index):
                sys.stdout.buffer.write(path + b"\0")
            return
        if any(path.rsplit(b"/", 1)[-1] == b".gitattributes" for path in index) or any(
            root.rglob(".gitattributes")
        ):
            raise UnsupportedRepository("Git attributes are unsupported by the Windows reader")
        # No synthetic success for unsupported index modes.
        for path, entry in index.iteritems():
            if not path_permitted(path.decode("utf-8"), scope):
                continue
            if isinstance(entry, ConflictedIndexEntry) or entry.mode not in (0o100644, 0o100755):
                raise UnsupportedRepository("conflicts, symlinks and submodules are unsupported")
            if entry.flags & 0x8000 or entry.extended_flags:
                raise UnsupportedRepository("special index flags are unsupported")
        if operation == "diff":
            paths = [path.encode("utf-8") for path in request["paths"]]
            if any(not path_permitted(path.decode("utf-8"), scope) for path in paths):
                raise UnsupportedRepository("diff path outside scope")
            normalizer = repo.get_blob_normalizer(config=repo.get_config())
            for path in paths:
                entry = index[path]
                if isinstance(entry, ConflictedIndexEntry):
                    raise UnsupportedRepository("conflicted index")
                old = repo.object_store[entry.sha]
                if not isinstance(old, Blob):
                    raise UnsupportedRepository("regular index entry must reference a blob")
                # Worktree reads must fail on access/IO errors, never appear deleted.
                # Use exact paths and preserve index executable bits (core.filemode=false).
                try:
                    new = normalizer.checkin_normalize(
                        Blob.from_string((root / path.decode("utf-8")).read_bytes()),
                        path,
                    )
                except (FileNotFoundError, IsADirectoryError):
                    new = None
                if new is not None and new.data == old.data:
                    continue
                memory = MemoryObjectStore()
                memory.add_object(old)
                if new is not None:
                    memory.add_object(new)
                write_object_diff(
                    sys.stdout.buffer,
                    memory,
                    (path, entry.mode, old.id),
                    (path, entry.mode, new.id) if new is not None else (None, None, None),
                )
            return
        if operation != "status":
            raise UnsupportedRepository("unsupported Git operation")
        changes = status(repo, untracked_files="normal")
        rows: dict[bytes, list[str]] = {}
        for kind, paths in changes.staged.items():
            for path in paths:
                rows.setdefault(path, [" ", " "])[0] = {
                    "add": "A",
                    "delete": "D",
                    "modify": "M",
                }[kind]
        for path in changes.unstaged:
            rows.setdefault(path, [" ", " "])[1] = (
                "M" if (root / path.decode("utf-8")).exists() else "D"
            )
        for path in changes.untracked:
            rows[path] = ["?", "?"]
        for path, columns in sorted(
            rows.items(), key=lambda item: (item[1] == ["?", "?"], item[0])
        ):
            name = path.decode("utf-8").replace("\\", "/")
            if path_permitted(name.rstrip("/"), scope):
                # Quote unusual filenames to keep one unambiguous entry per line.
                rendered = (
                    json.dumps(name, ensure_ascii=False)
                    if any(char.isspace() or char in '\\"' for char in name)
                    else name
                )
                sys.stdout.buffer.write(("".join(columns) + " " + rendered + "\n").encode("utf-8"))


if __name__ == "__main__":
    try:
        main()
    except UnsupportedRepository as error:
        print(f"Windows Git reader unsupported: {error}", file=sys.stderr)
        sys.exit(1)
    except Exception as error:
        # Never print untrusted exception payloads which can include object contents.
        print(f"Windows Git reader failed: {type(error).__name__}", file=sys.stderr)
        sys.exit(1)
