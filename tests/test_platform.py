from tests.base_test import BaseTest

from git_machete.platform import Domain, find_access_token_for_domain


class TestPlatform(BaseTest):
    def test_find_access_token_for_domain(self):
        class ExampleDomain(Domain):
            DEFAULT = "default.domain.com"

            def __init__(self, value: str):
                self.value = value

        lines = [
            "defaulttoken",
            "explicitdefaulttoken default.domain.com",
            "mytoken_for_git_example_com git.example.com",
            "myothertoken_for_git_example_org git.example.org",
            "yetanothertoken_for_git_example_com git.example.com",
        ]

        assert find_access_token_for_domain(
            lines, ExampleDomain("git.example.com")
        ) == "mytoken_for_git_example_com"

        assert find_access_token_for_domain(
            lines, ExampleDomain(ExampleDomain.DEFAULT)
        ) == "defaulttoken"

        assert find_access_token_for_domain(
            lines, ExampleDomain("nonexistent.com")
        ) == None
