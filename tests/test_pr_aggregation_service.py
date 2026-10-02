import unittest

from services.PRAggregationService import PRAggregationService


class PRAggregationServiceTests(unittest.TestCase):
    def setUp(self):
        self.repos = [
            {"id": "r1", "owner": "example-workspace", "slug": "backend-service"},
            {"id": "r2", "owner": "example-workspace", "slug": "frontend-service"},
        ]

    def test_seed_cursor_state(self):
        state = PRAggregationService.seed_cursor_state(self.repos)
        self.assertEqual(state, {"r1": "", "r2": ""})

    def test_repos_for_page_reset_fetches_all(self):
        pairs = PRAggregationService.repos_for_page(self.repos, {"r1": "x", "r2": ""}, reset=True)
        self.assertEqual(len(pairs), 2)

    def test_repos_for_page_non_reset_fetches_only_with_next(self):
        pairs = PRAggregationService.repos_for_page(self.repos, {"r1": "next-url", "r2": ""}, reset=False)
        self.assertEqual(len(pairs), 1)
        self.assertEqual(pairs[0][0]["id"], "r1")
        self.assertEqual(pairs[0][1], "next-url")

    def test_normalize_next_state(self):
        normalized = PRAggregationService.normalize_next_state(self.repos, {"r1": "next1"})
        self.assertEqual(normalized, {"r1": "next1", "r2": ""})

    def test_repository_without_provider_id_can_paginate(self):
        repo = {"provider": "bitbucket", "owner": "workspace", "slug": "repo"}
        key = "bitbucket:workspace/repo"
        self.assertEqual(PRAggregationService.seed_cursor_state([repo]), {key: ""})
        self.assertEqual(PRAggregationService.repos_for_page([repo], {key: "next"}, reset=False), [(repo, "next")])

    def test_has_more(self):
        self.assertFalse(PRAggregationService.has_more({"r1": "", "r2": ""}))
        self.assertTrue(PRAggregationService.has_more({"r1": "next", "r2": ""}))


if __name__ == "__main__":
    unittest.main()
