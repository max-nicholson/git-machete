import os
import re
import shutil
import urllib.parse
from typing import NamedTuple, Optional, Tuple
from typing_extensions import Self


from .exceptions import UnexpectedMacheteException
from .platform import AccessToken, Domain, Platform, PlatformClient, find_access_token_for_domain
from .utils import debug, popen_cmd, slurp_file


class GitLabDomain(Domain):
    DEFAULT = 'gitlab.com'

    def __init__(self, value: Optional[str]) -> None:
        self.value = value or self.DEFAULT

    def url_prefix_for(self, *, path: str) -> str:
        return f'https://{self.value}{"/api" if path == "/graphql" else "/api/v4"}'


class GitLabPlatform(Platform):
    ALIAS = 'GitLab'


# Same as glab CLI tool
# https://gitlab.com/gitlab-org/cli/-/blob/6ac665d5891eb2044a09488f39a1125822b872a3/internal/config/config_mapping.go#L37
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

class GitLabCurrentUser(NamedTuple):
    username: str

    def __str__(self) -> str:
        return self.username

    @classmethod
    def from_json(cls, json: dict) -> Self:
        return cls(username=json['username'])


class GitLabMergeRequest(NamedTuple):
    @staticmethod
    def template(*, project_root: str) -> Optional[str]:
        # https://docs.gitlab.com/ee/user/project/description_templates.html#create-a-merge-request-template
        # https://docs.gitlab.com/ee/user/project/description_templates.html#set-a-default-template-for-merge-requests-and-issues
        # Actual MR template resolution for GitLab has a complex hierarchy of templates, including
        # project-level, group-level and instance-level templates - and also the ability to "choose"
        # a template
        # To keep things simple, we'll only support the "Default.md" template for now
        # (similar to GitHub), but could consider prompting to select a template e.g. glab CLI
        # https://gitlab.com/gitlab-org/cli/-/blob/6ac665d5891eb2044a09488f39a1125822b872a3/commands/mr/create/mr_create.go#L399
        # or a config setting
        template_path = os.path.join(project_root, '.gitlab', 'merge_request_templates', 'Default.md')
        if os.path.isfile(template_path):
                return slurp_file(template_path)


"""
A namespace contains a collection of projects and/or groups, and is either:
- a user's personal namespace e.g. https://gitlab.example.com/alex
- a group namespace e.g. https://gitlab.example.com/my-group
- a subgroup namespace e.g. https://gitlab.example.com/my-group/my-subgroup/my-subsubgroup

In the 3rd case, there can be up to 20 levels of nesting

https://docs.gitlab.com/ee/user/namespace/
"""
class Namespace(NamedTuple):
    full_path: str


class Project(NamedTuple):
    id: Optional[str]
    path: Optional[str]
    namespace: Optional[Namespace]

    @property
    def path_with_namespace(self) -> Optional[str]:
        return f'{self.namespace.full_path}/{self.path}' if self.namespace and self.path else None

    def uri_param(self) -> str:
        if self.id:
            return self.id

        if self.path_with_namespace:
            # https://docs.gitlab.com/ee/api/rest/index.html#namespaced-path-encoding
            return urllib.parse.quote(self.path_with_namespace, safe='')

        raise UnexpectedMacheteException("Cannot generate a URL param for a project without an ID or a path with namespace")


class GitLabClient(PlatformClient):
    DEFAULT_HEADERS = {
        'Content-Type': 'application/json',
        'User-Agent': 'git-machete',
        'Accept': 'application/json',
    }

    def __init__(self, domain: GitLabDomain, project: Project) -> None:
        self.__domain = domain
        self.__project = project
        self.__token: Optional[GitLabToken] = GitLabToken.for_domain(domain)

    def get_current_user_login(self) -> Optional[str]:
        if not self.__token:
            return None

        # https://docs.gitlab.com/ee/api/users.html#list-current-user
        current_user = GitLabCurrentUser.from_json(self.__fire_api_request('GET', '/user'))
        return current_user.username

    def create_merge_request(self, *, head: str, base: str, title: str, description: str, is_draft: bool) -> None:
        if not self.__token:
            raise UnexpectedMacheteException("Cannot create a merge request without a GitLab token")

        # https://docs.gitlab.com/ee/api/merge_requests.html#create-mr
        self.__fire_api_request(
            'POST',
            f'/projects/{self.__project.uri_param()}/merge_requests',
            request_body={
                'source_branch': head,
                'target_branch': base,
                # NB: No `draft` param in the API, can only be achieved with a prefix in the title
                # https://docs.gitlab.com/ee/user/project/merge_requests/drafts.html#mark-merge-requests-as-drafts
                # "Creating or editing a merge request: Add [Draft], Draft: or (Draft) to the
                # beginning of the merge request’s title, or select Mark as draft below the Title
                # field."
                'title': title if not is_draft else f'[Draft] {title}',
                'description': description,
            }
        )
