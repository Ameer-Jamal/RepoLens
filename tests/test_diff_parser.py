import unittest

from ui.diff_parser import parse_unified_diff, DiffFile, DiffHunk, DiffLine


class DiffParserTests(unittest.TestCase):
    def test_header_like_code_lines_keep_their_line_numbers(self):
        files = parse_unified_diff(
            "diff --git a/doc.txt b/doc.txt\n--- a/doc.txt\n+++ b/doc.txt\n"
            "@@ -1,2 +1,2 @@\n--- old heading\n+++ new heading\n unchanged\n"
        )
        self.assertEqual((files[0].additions, files[0].deletions), (1, 1))
        lines = files[0].hunks[0].lines
        self.assertEqual([line.content for line in lines], ["-- old heading", "++ new heading", "unchanged"])
        self.assertEqual((lines[-1].old_line_num, lines[-1].new_line_num), (2, 2))

    def test_parse_empty_diff(self):
        self.assertEqual(parse_unified_diff(""), [])
        self.assertEqual(parse_unified_diff("   \n  "), [])

    def test_parse_single_file_diff(self):
        diff_text = """diff --git a/services/auth.py b/services/auth.py
index 1234567..89abcdef 100644
--- a/services/auth.py
+++ b/services/auth.py
@@ -10,3 +10,4 @@ def test():
     a = 1
-    b = 2
+    b = 3
+    c = 4
     return a + b
"""
        files = parse_unified_diff(diff_text)
        self.assertEqual(len(files), 1)
        f = files[0]
        self.assertEqual(f.old_path, "services/auth.py")
        self.assertEqual(f.new_path, "services/auth.py")
        self.assertEqual(f.display_path, "services/auth.py")
        self.assertEqual(f.change_type, "M")
        self.assertEqual(f.additions, 2)
        self.assertEqual(f.deletions, 1)
        self.assertEqual(len(f.hunks), 1)

        hunk = f.hunks[0]
        self.assertEqual(hunk.old_start, 10)
        self.assertEqual(hunk.new_start, 10)
        self.assertEqual(len(hunk.lines), 5)

        del_line = hunk.lines[1]
        self.assertEqual(del_line.line_type, "del")
        self.assertEqual(del_line.old_line_num, 11)
        self.assertIsNone(del_line.new_line_num)
        self.assertEqual(del_line.content, "    b = 2")

        add_line = hunk.lines[2]
        self.assertEqual(add_line.line_type, "add")
        self.assertIsNone(add_line.old_line_num)
        self.assertEqual(add_line.new_line_num, 11)
        self.assertEqual(add_line.content, "    b = 3")

    def test_parse_added_and_deleted_files(self):
        diff_text = """diff --git a/new_file.txt b/new_file.txt
new file mode 100644
--- /dev/null
+++ b/new_file.txt
@@ -0,0 +1,2 @@
+line 1
+line 2
diff --git a/old_file.txt b/old_file.txt
deleted file mode 100644
--- a/old_file.txt
+++ /dev/null
@@ -1,1 +0,0 @@
-goodbye
"""
        files = parse_unified_diff(diff_text)
        self.assertEqual(len(files), 2)

        f1 = files[0]
        self.assertEqual(f1.change_type, "A")
        self.assertEqual(f1.display_path, "new_file.txt")
        self.assertEqual(f1.additions, 2)
        self.assertEqual(f1.deletions, 0)

        f2 = files[1]
        self.assertEqual(f2.change_type, "D")
        self.assertEqual(f2.display_path, "old_file.txt")
        self.assertEqual(f2.additions, 0)
        self.assertEqual(f2.deletions, 1)


if __name__ == "__main__":
    unittest.main()
