import difflib
import random
import unittest

from src.diff import Unsupported, parse_patch


class DiffTests(unittest.TestCase):
    def test_creation_and_deletion_validate_null_headers_and_entire_file(self):
        add=('diff --git a/tests/fixtures/new.sql b/tests/fixtures/new.sql\n'
             'new file mode 100644\nindex 0000000..abcdef1\n'
             '--- /dev/null\n+++ b/tests/fixtures/new.sql\n@@ -0,0 +1 @@\n+SELECT 1;\n')
        change=parse_patch(add)[0]
        self.assertEqual(change.kind,'add')
        self.assertEqual(change.apply('')[0],'SELECT 1;\n')
        with self.assertRaises(Unsupported):
            change.apply('already exists\n')
        delete=('diff --git a/case b/case\ndeleted file mode 100644\n'
                '--- a/case\n+++ /dev/null\n@@ -1 +0,0 @@\n-old\n')
        change=parse_patch(delete)[0]
        self.assertEqual(change.kind,'delete')
        self.assertEqual(change.apply('old\n')[0],'')
        with self.assertRaises(Unsupported):
            change.apply('old\nleftover\n')
        for invalid in [add.replace('100644','120000'),add.replace('--- /dev/null','--- a/tests/fixtures/new.sql')]:
            with self.assertRaises(Unsupported):
                parse_patch(invalid)

    def test_seeded_round_trip_without_checkout_writes(self):
        rng = random.Random(83)
        for _ in range(100):
            original = [f"line {n}\n" for n in range(rng.randrange(4, 28))]
            changed = original.copy()
            for n in range(4):
                start = rng.randrange(len(changed) + 1)
                end = min(start + rng.randrange(3), len(changed))
                changed[start:end] = [f"insert {n}.{i}\n" for i in range(rng.randrange(1, 4))]
            patch = "".join(difflib.unified_diff(original, changed, "a/case", "b/case",
                                                n=rng.randrange(4)))
            replacement, edits = parse_patch(patch)[0].apply("".join(original))
            self.assertEqual(replacement, "".join(changed))
            self.assertTrue(edits)

    def test_final_newline_and_literal_unicode_separators(self):
        patch = (
            "diff --git a/case b/case\nindex abcde12..123abcd 100644\n"
            "--- a/case\n+++ b/case\n@@ -1 +1 @@\n"
            "-one\u2028two\n\\ No newline at end of file\n+three\u000cfour\n"
        )
        self.assertEqual(parse_patch(patch)[0].apply("one\u2028two")[0], "three\u000cfour\n")

    def test_inconsistent_new_offset_and_truncated_hunks(self):
        for hunk in ("@@ -1 +2 @@\n-old\n+new\n", "@@ -1,2 +1,2 @@\n-old\n+new\n"):
            patch = "--- a/case\n+++ b/case\n" + hunk
            with self.assertRaises(Unsupported):
                parse_patch(patch)[0].apply("old\n")


if __name__ == "__main__":
    unittest.main()
