from unittest.mock import MagicMock, mock_open

from pytest_mock import MockerFixture

from git_machete.gitlab import GitLabDomain, GitLabToken
from tests.base_test import BaseTest
from tests.mockers import mock__popen_cmd_with_fixed_results, overridden_environment

from tests.mockers_github import (mock_shutil_which)


class TestGitLabToken(BaseTest):
    def test_get_token_from_env_var(self) -> None:
        with overridden_environment(GITLAB_TOKEN='gitlab_token_from_env_var'):
            gitlab_token = GitLabToken.for_domain(domain=GitLabDomain(GitLabDomain.DEFAULT))

        assert gitlab_token is not None
        assert gitlab_token.provider == '`GITLAB_TOKEN` environment variable'
        assert gitlab_token.value == 'gitlab_token_from_env_var'

    # Note that tox doesn't pass env vars from its env to the processes by default,
    # so we don't need to mock away GITLAB_TOKEN in the following tests, even if it's present in the env.
    # This doesn't cover the case of running from outside tox (e.g. via IntelliJ),
    # so hiding GITLAB_TOKEN might eventually become necessary.

    def test_get_token_from_file_in_home_directory(self, mocker: MockerFixture) -> None:
        gitlab_token_contents = ('mytoken_for_gitlab_com\n'
                                 'myothertoken_for_git_example_org git.example.org\n'
                                 'yetanothertoken_for_git_example_com git.example.com')
        self.patch_symbol(mocker, 'builtins.open', mock_open(read_data=gitlab_token_contents))

        domain = GitLabDomain(GitLabDomain.DEFAULT)
        gitlab_token = GitLabToken.for_domain(domain=domain)
        assert gitlab_token is not None
        assert gitlab_token.provider == f'auth token for {domain} from `~/.gitlab-token`'
        assert gitlab_token.value == 'mytoken_for_gitlab_com'

        # Line ends with \n
        domain = GitLabDomain('git.example.org')
        gitlab_token = GitLabToken.for_domain(domain=domain)
        assert gitlab_token is not None
        assert gitlab_token.provider == f'auth token for {domain} from `~/.gitlab-token`'
        assert gitlab_token.value == 'myothertoken_for_git_example_org'

        # Last line, doesn't end with \n
        domain = GitLabDomain('git.example.com')
        gitlab_token = GitLabToken.for_domain(domain=domain)
        assert gitlab_token is not None
        assert gitlab_token.provider == f'auth token for {domain} from `~/.gitlab-token`'
        assert gitlab_token.value == 'yetanothertoken_for_git_example_com'

        domain = GitLabDomain('git.example.net')
        gitlab_token = GitLabToken.for_domain(domain=domain)
        assert gitlab_token is None

    def test_get_token_from_glab(self, mocker: MockerFixture) -> None:
        file_open_mock = MagicMock()
        file_open_mock.side_effect = FileNotFoundError()
        self.patch_symbol(mocker, 'builtins.open', mock_open(file_open_mock))
        self.patch_symbol(mocker, 'shutil.which', mock_shutil_which('/path/to/glab'))

        domain = GitLabDomain('git.example.com')

        fixed_popen_cmd_results = [(1, "unknown error", "")]
        self.patch_symbol(mocker, 'git_machete.utils._popen_cmd', mock__popen_cmd_with_fixed_results(*fixed_popen_cmd_results))
        gitlab_token = GitLabToken.for_domain(domain=domain)
        assert gitlab_token is None

        fixed_popen_cmd_results = [(0, "glab version 1.13.0 (2099-12-31)", ""),
                                   (0, "", 'unknown command "status" for "glab auth"')]
        self.patch_symbol(mocker, 'git_machete.utils._popen_cmd', mock__popen_cmd_with_fixed_results(*fixed_popen_cmd_results))
        gitlab_token = GitLabToken.for_domain(domain=domain)
        assert gitlab_token is None

        fixed_popen_cmd_results = [(0, "glab version 1.36.0\n", ""),
                                   (0, "", "No GitLab instance has been authenticated with glab. Run `glab auth login` to authenticate.\n")]
        self.patch_symbol(mocker, 'git_machete.utils._popen_cmd', mock__popen_cmd_with_fixed_results(*fixed_popen_cmd_results))
        gitlab_token = GitLabToken.for_domain(domain=domain)
        assert gitlab_token is None

        fixed_popen_cmd_results = [(0, "glab version 1.36.0\n", ""),
                                   (0, "", r"""'git.example.com'
  x git.example.com: api call failed: GET https://git.example.com/api/v4/user: 401 {message: 401 Unauthorized}
  ✓ Git operations for git.example.com configured to use ssh protocol.
  ✓ API calls for git.example.com are made over https protocol
  ✓ REST API Endpoint: https://git.example.com/api/v4/
  ✓ GraphQL Endpoint: https://git.example.com/api/graphql/
  x No token provided""")]
        self.patch_symbol(mocker, 'git_machete.utils._popen_cmd', mock__popen_cmd_with_fixed_results(*fixed_popen_cmd_results))
        gitlab_token = GitLabToken.for_domain(domain=domain)
        assert gitlab_token is None

        fixed_popen_cmd_results = [(0, "glab version 1.36.0\n", ""),
                                   (0, "", r"""'git.example.com'
  ✓ Logged in to git.example.com as bob (C:\Users\bob\.config\glab-cli/config.yml)
  ✓ Git operations for git.example.com configured to use https protocol.
  ✓ API calls for git.example.com are made over https protocol
  ✓ REST API Endpoint: https://git.example.com/api/v4/
  ✓ GraphQL Endpoint: https://git.example.com/api/graphql/
  ✓ Token: mytoken_for_gitlab_com_from_glab_cli""")]
        self.patch_symbol(mocker, 'git_machete.utils._popen_cmd', mock__popen_cmd_with_fixed_results(*fixed_popen_cmd_results))
        gitlab_token = GitLabToken.for_domain(domain=domain)
        assert gitlab_token is not None
        assert gitlab_token.provider == f'auth token for {domain} from `glab` GitLab CLI'
        assert gitlab_token.value == 'mytoken_for_gitlab_com_from_glab_cli'
