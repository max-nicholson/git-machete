import os
import re
import shutil
from typing import NamedTuple, Optional, Tuple
from typing_extensions import Self


from .exceptions import UnexpectedMacheteException
from .platform import AccessToken, Domain, find_access_token_for_domain
from .utils import debug, popen_cmd


class GitLabDomain(Domain):
    DEFAULT = 'gitlab.com'

    def __init__(self, value: Optional[str]) -> None:
        self.value = value or self.DEFAULT


# Same as glab CLI tool
GITLAB_TOKEN_ENV_VAR = 'GITLAB_TOKEN'


class GitLabToken(NamedTuple):
    value: str
    provider: str

    @classmethod
    def for_domain(cls, domain: GitLabDomain) -> Optional[Self]:
        return (cls.__get_token_from_env() or
                cls.__get_token_from_file_in_home_directory(domain) or
                cls.__get_token_from_glab(domain))

    @classmethod
    def __get_token_from_env(cls) -> Optional[Self]:
        debug(f"1. Trying to find token in `{GITLAB_TOKEN_ENV_VAR}` environment variable...")
        gitlab_token = os.environ.get(GITLAB_TOKEN_ENV_VAR)
        if gitlab_token:
            return cls(value=gitlab_token,
                       provider=f'`{GITLAB_TOKEN_ENV_VAR}` environment variable')
        return None

    @classmethod
    def __get_token_from_file_in_home_directory(cls, domain: GitLabDomain) -> Optional[Self]:
        file_path = "~/.gitlab-token"
        debug(f"2. Trying to find token in `{file_path}`...")
        provider = f'auth token for {domain} from `{file_path}`'
        file_full_path = os.path.expanduser(file_path)

        try:
            with open(file_full_path) as f:
                debug(f"  File `{file_full_path}` exists")

                token = find_access_token_for_domain(f.readlines(), domain)
                if token:
                    return cls(value=token, provider=provider)
        except FileNotFoundError:
            return None

        return None

    @classmethod
    def __get_token_from_glab(cls, domain: GitLabDomain) -> Optional[Self]:
        debug("3. Trying to find token via `glab` GitLab CLI...")
        # Abort without error if `glab` isn't available
        glab = shutil.which('glab')
        if not glab:
            return None

        glab_version_returncode, glab_version_stdout, _ = popen_cmd(glab, "--version")
        if glab_version_returncode != 0:
            return None

        # The stdout of `glab --version` looks like:
        #
        # glab version 1.36.0\n

        glab_version_match = re.search(r"glab version (\d+).(\d+).(\d+)", glab_version_stdout)
        glab_version: Optional[Tuple[int, int, int]] = None

        if glab_version_match:
            glab_version = int(glab_version_match.group(1)), int(glab_version_match.group(2)), int(glab_version_match.group(3))
        else:
            raise UnexpectedMacheteException(f"Could not parse output of `glab --version`: `{glab_version_stdout}`")

        if not glab_version or glab_version < (1, 14, 0):
            # `glab auth status` added on 1.14.0
            # https://gitlab.com/gitlab-org/cli/-/commit/d52b76a779e5f20678dfd1e1e769949204e61dc9
            return None

        glab_token_returncode, _, glab_token_stderr = \
            popen_cmd(glab, "auth", "status", "--hostname", domain.value, "--show-token", hide_debug_output=True)
        if glab_token_returncode != 0:
            return None

        # The stderr of `glab auth status --show-token` looks like:
        #
        # {domain}:
        #   ✓ ✓ Logged in to {domain} as {username} ({config_path})
        #   ✓ Git operations for {domain} configured to use {protocol} protocol.
        #   ✓ API calls for gitlab.com are made over {protocol} protocol
        #   ✓ REST API Endpoint: {protocol}://{domain}/api/v4/
        #   ✓ GraphQL Endpoint: {protocol}://{domain}/api/graphql/
        #   ✓ Token: <token>

        match = re.search(r"Token: (\w+)", glab_token_stderr)
        if match:
            return cls(value=match.group(1), provider=f'auth token for {domain} from `glab` GitLab CLI')

        return None

    @classmethod
    def get_possible_providers(cls) -> str:
        return (f'\n\t1. `{GITLAB_TOKEN_ENV_VAR}` environment variable\n'
                '\t2. Content of the `~/.gitlab-token` file\n'
                '\t3. Current auth token from the `glab` GitLab CLI\n')
