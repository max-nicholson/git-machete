import copy
import http
import json
import re
import urllib.error
# Deliberately NOT using much more convenient `requests` to avoid external dependencies in production code
import urllib.request
from typing import Any, Dict, List, Literal, Optional
from typing_extensions import Protocol, Self

from .exceptions import MacheteException, UnexpectedMacheteException, UnprocessableEntityHTTPError
from .utils import bold, compact_dict, debug, warn

class Domain(Protocol):
    DEFAULT: str

    value: str

    def __str__(self) -> str:
        return self.value

    def url_prefix_for(self, *, path: str) -> str:
        ...


PlatformAlias = Literal['GitHub', 'GitLab']


class Platform(Protocol):
    ALIAS: PlatformAlias


def find_access_token_for_domain(lines: List[str], domain: Domain) -> Optional[str]:
    # ~/.[platform]-token is a file with a structure similar to:
    #
    # mytoken_for_[platform]_com
    # myothertoken_for_git_example_org git.example.org
    # yetanothertoken_for_git_example_com git.example.com

    for line in lines:
        line = line.rstrip()
        if line.endswith(" " + domain.value):
            token = line.split(" ")[0]
            return token
        elif domain.value == domain.DEFAULT and " " not in line:
            return line


class AccessToken(Protocol):
    value: str
    provider: str

    @classmethod
    def for_domain(cls, domain: Domain) -> Optional[Self]:
        ...

    @classmethod
    def get_possible_providers(cls) -> str:
        ...


class PlatformClient(Protocol):
    DEFAULT_HEADERS: Dict[str, str]

    __domain: Domain
    __token: Optional[AccessToken]

    @staticmethod
    def __extract_failure_info_from_422(response: Any) -> str:
        if response['message'] != 'Validation Failed':
            return str(response['message'])
        ret: List[str] = []
        if response.get('errors'):
            # GraphQL Response with `errors`
            for error in response['errors']:
                if error.get('message'):
                    ret.append(error['message'])
                else:
                    ret.append(str(error))
        if ret:
            return '\n'.join(ret)
        else:
            return str(response)

    def __fire_api_request(self, method: str, path: str, request_body: Optional[Dict[str, Any]] = None) -> Any:
        headers: Dict[str, str] = copy.copy(self.DEFAULT_HEADERS)
        if self.__token:
            headers['Authorization'] = 'Bearer ' + self.__token.value

        url_prefix = self.__domain.url_prefix_for(path=path)

        url = url_prefix + path
        json_body: Optional[str] = json.dumps(request_body) if request_body else None
        http_request = urllib.request.Request(url, headers=headers, data=json_body.encode() if json_body else None, method=method.upper())
        debug(f'firing a {method} request to {url} with {"a" if self.__token else "no"} '
              f'bearer token and request body {compact_dict(request_body) if request_body else "<none>"}')

        try:
            with urllib.request.urlopen(http_request) as response:
                parsed_response_body: Any = json.loads(response.read().decode())
                # GitHub and GitLab use the same format for the Link header
                # https://docs.github.com/en/rest/guides/using-pagination-in-the-rest-api?apiVersion=2022-11-28#using-link-headers
                # https://docs.gitlab.com/ee/api/rest/index.html#pagination-link-header
                link_header: str = response.info()["link"]
                if link_header:
                    url_prefix_regex = re.escape(url_prefix)
                    match = re.search(f'<{url_prefix_regex}(/[^>]+)>; rel="next"', link_header)
                    if match:
                        next_page_path = match.group(1)
                        debug(f'link header is present in the response, and there is more data to retrieve under {next_page_path}')
                        return parsed_response_body + self.__fire_api_request(method, next_page_path, request_body)
                    else:
                        debug('link header is present in the response, but there is no more data to retrieve')
                return parsed_response_body
        except urllib.error.HTTPError as err:
            if err.code == http.HTTPStatus.UNPROCESSABLE_ENTITY:
                error_response = json.loads(err.read().decode())
                error_reason: str = self.__extract_failure_info_from_422(error_response)
                raise UnprocessableEntityHTTPError(error_reason)
            elif err.code in (http.HTTPStatus.UNAUTHORIZED, http.HTTPStatus.FORBIDDEN):
                first_line = f'GitHub API returned `{err.code}` HTTP status with error message: `{err.reason}`\n'
                last_line = 'You can also use a different token provider, available providers can be found via `git machete help github`.'
                if self.__token:
                    raise MacheteException(
                        first_line + 'Make sure that the GitHub API token '
                                     f'provided by the {self.__token.provider} '
                                     f'is valid and allows for access to `{method.upper()}` `{url_prefix}{path}`.\n' + last_line)
                else:
                    raise MacheteException(
                        first_line + 'You might not have the required permissions for this repository.\n'
                                     'Provide a GitHub API token with `repo` access.\n'
                                     f'Visit `https://{self.__domain}/settings/tokens` to generate a new one.\n' + last_line)
            elif err.code == http.HTTPStatus.NOT_FOUND:
                # TODO (#164): make a dedicated exception here
                raise MacheteException(
                    f'`{method} {url}` request ended up in 404 response from GitHub. A valid GitHub API token is required.\n'
                    f'Provide a GitHub API token with `repo` access via one of the: {self.__token.get_possible_providers()} '
                    f'Visit `https://{self.__domain}/settings/tokens` to generate a new one.')
            # See https://stackoverflow.com/a/62385184 for why 307 for POST/PATCH isn't automatically followed by urllib,
            # unlike 307 for GET, or 301/302 for all HTTP methods.
            elif err.code == http.HTTPStatus.TEMPORARY_REDIRECT:
                # err.headers is a case-insensitive dict of class Message with the `__getitem__` and `get` functions implemented in
                # https://github.com/python/cpython/blob/3.10/Lib/email/message.py
                location = err.headers['Location']
                if location is not None:
                    # The URL returned in the `Location` header is of the form "https://api.github.com/repositories/453977473".
                    # It doesn't contain the info about the new org/repo name, which we'd like to display to the user in a warning.
                    match = re.search('/repositories/([0-9]+)/', location)
                    if match:
                        new_org_and_repo = self.get_org_and_repo_names_by_id(match.group(1))
                    else:
                        raise UnexpectedMacheteException(
                            f"Could not extract organization and repository from Location header: `{location}`.")
                else:
                    raise UnexpectedMacheteException(
                        f'GitHub API returned `{err.code}` HTTP status with error message: `{err.reason}`.\n'
                        'It looks like the organization or repository name got changed recently and is outdated.\n'
                        'Update your remote repository manually via: `git remote set-url <remote_name> <new_repository_url>`.')
                new_path = re.sub("https://[^/]+", "", location)
                result = self.__fire_api_request(method=method, path=new_path, request_body=request_body)
                warn(f'GitHub API returned `{err.code}` HTTP status with error message: `{err.reason}`.\n'
                     'It looks like the organization or repository name got changed recently and is outdated.\n'
                     f'New organization is {bold(new_org_and_repo.split("/")[0])} and '
                     f'new repository is {bold(new_org_and_repo.split("/")[1])}.\n'
                     'You can update your remote repository via: `git remote set-url <remote_name> <new_repository_url>`.')
                return result
            else:
                raise UnexpectedMacheteException(f'GitHub API returned `{err.code}` HTTP status with error message: `{err.reason}`.')
        except OSError as e:  # pragma: no cover
            raise MacheteException(f'Could not connect to {url_prefix}: {e}')

    def __fire_graphql_api_request(self, *, query: str, variables: Optional[Dict[str, Any]] = None) -> Any:
        request_body: Dict[str, Any] = {
            'query': query,
        }
        if variables:
            request_body['variables'] = variables

        return self.__fire_api_request(
            method='POST',
            path='/graphql',
            request_body=request_body,
        )

    def get_current_user_login(self) -> Optional[str]:
        ...
