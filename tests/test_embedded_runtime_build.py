"""Offline checks for the embedded runtime build recipe."""

import hashlib
from io import BytesIO
from pathlib import Path
import stat
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from zipfile import ZipFile, ZipInfo

from bootstrap import embedded_runtime_build as recipe


class RuntimeRecipeTests(unittest.TestCase):
    def test_offline_cache_checks_the_exact_bytes(self):
        url = "https://www.python.org/ftp/python/fixture.zip"
        original = b"locked fixture archive"
        digest = hashlib.sha256(original).hexdigest()
        with TemporaryDirectory() as directory, patch.object(recipe, "urlopen") as open_url:
            cache = Path(directory)
            cached = cache / "fixture.zip"
            cached.write_bytes(original)
            self.assertEqual(recipe.artifact(cache, url, digest), (cached.name, original))
            cached.write_bytes(b"changed fixture archive")
            with self.assertRaisesRegex(ValueError, "digest or length mismatch"):
                recipe.artifact(cache, url, digest)
            open_url.assert_not_called()

    def test_archive_refuses_platform_paths_links_and_case_aliases(self):
        for name in ("../escape", "site/../../escape", "C:/absolute.txt", "item/COM1.dll", "item\\file"):
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "Unsafe archive path"):
                recipe.relative_name(name)

        linked = ZipInfo("link.dll")
        linked.create_system = 3
        linked.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive = BytesIO()
        with ZipFile(archive, "w") as writer:
            writer.writestr(linked, "python314.dll")
        with TemporaryDirectory() as directory, ZipFile(archive) as source:
            writer = recipe.Extract(Path(directory))
            with self.assertRaisesRegex(ValueError, "Unexpected file type"):
                writer.archive(source)
            writer.write(recipe.relative_name("Runtime/first.dll"), b"one")
            with self.assertRaisesRegex(ValueError, "path casing"):
                writer.write(recipe.relative_name("runtime/second.dll"), b"two")

    def test_source_tree_reads_the_selected_git_object(self):
        with TemporaryDirectory() as directory:
            source = Path(directory)
            recipe.git(source, "init", "--quiet")
            for relative in recipe.APP_FILES:
                path = source / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"# committed\n")
            recipe.git(source, "add", "tools")
            recipe.git(source, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.test",
                       "-c", "commit.gpgSign=false", "commit", "--quiet", "-m", "Embedded app fixture")
            revision = recipe.git(source, "rev-parse", "HEAD").decode("ascii").strip()
            entry = "tools/silverfish/embedded_startup.py"
            (source / entry).write_bytes(b"# uncommitted\n")
            blobs = recipe.source_tree(source, revision)
            self.assertEqual(set(blobs), recipe.APP_FILES)
            self.assertEqual(recipe.git(source, "cat-file", "blob", blobs[entry]), b"# committed\n")
            with self.assertRaisesRegex(ValueError, "full lowercase Git commit"):
                recipe.source_tree(source, "HEAD")
