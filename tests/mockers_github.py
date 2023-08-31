import json
import re
from collections import defaultdict
from contextlib import AbstractContextManager, contextmanager
from http import HTTPStatus
from typing import Any, Callable, Dict, Iterator, List, Optional, Union
from urllib.error import HTTPError
from urllib.parse import ParseResult, parse_qs, urlencode, urlparse
from urllib.request import Request

from git_machete.github import GitHubToken, OrganizationAndRepository

mock_repository_info: Dict[str, str] = {'full_name': 'tester/repo_sandbox',
                                        'html_url': 'https://github.com/tester/repo_sandbox.git'}


def mock_github_token_for_domain_none(_domain: str) -> None:
    return None


def mock_github_token_for_domain_fake(_domain: str) -> GitHubToken:
    return GitHubToken(value='ghp_dummy_token', provider='dummy_provider')


def mock_from_url(domain: str, url: str) -> "OrganizationAndRepository":  # noqa: U100
    return OrganizationAndRepository("example-org", "example-repo")


def mock_shutil_which(path: Optional[str]) -> Callable[[Any], Optional[str]]:
    return lambda _cmd: path


class MockGitHubAPIResponse:
    def __init__(self,
                 status_code: int,
                 response_data: Union[List[Dict[str, Any]], Dict[str, Any]],
                 headers: Dict[str, Any] = {}) -> None:
        self.status_code = status_code
        self.response_data = response_data
        self.headers = headers

    def read(self) -> bytes:
        return json.dumps(self.response_data).encode()

    def info(self) -> Dict[str, Any]:
        return defaultdict(lambda: "", self.headers)


class MockGitHubAPISearchResponse(MockGitHubAPIResponse):
    def __init__(self, response_data: List[Dict[str, Any]]) -> None:
        super().__init__(
            status_code=200,
            response_data={
                "total_count": len(response_data),
                "incomplete_results": False,
                "items": response_data
            }
        )


class MockGitHubAPIState:
    def __init__(self, pulls: List[Dict[str, Any]]) -> None:
        self.__pulls: List[Dict[str, Any]] = [dict(pull) for pull in pulls]

    def get_pull_by_number(self, pull_no: str) -> Optional[Dict[str, Any]]:
        for pull in self.__pulls:
            if pull['number'] == pull_no:
                return pull
        return None

    def get_open_pulls(self) -> List[Dict[str, Any]]:
        return [pull for pull in self.__pulls if pull['state'] == 'open']

    def get_open_pulls_for_heads(self, heads: List[str]) -> List[Dict[str, Any]]:
        return [pull for pull in self.get_open_pulls() if pull['head']['ref'] in heads]

    def get_open_pulls_for_authors(self, authors: List[str]) -> List[Dict[str, Any]]:
        return [pull for pull in self.get_open_pulls() if pull['user']['login'] in authors]

    def get_pull_by_head_and_base(self, head: str, base: str) -> Optional[Dict[str, Any]]:
        for pull in self.__pulls:
            pull_head: str = pull['head']['ref']
            pull_base: str = pull['base']['ref']
            if (head, base) == (pull_head, pull_base):
                return pull
        return None

    def add_pull(self, pull: Dict[str, Any]) -> None:
        pull_numbers = [int(item['number']) for item in self.__pulls]
        pull['number'] = str(max(pull_numbers or [0]) + 1)
        self.__pulls.append(pull)


class MockHTTPError(HTTPError):
    from email.message import Message

    def __init__(self, url: str, code: int, msg: Any, hdrs: Message, fp: Any) -> None:
        super().__init__(url, code, msg, hdrs, fp)
        self.msg = msg

    def read(self, _n: int = 1) -> bytes:  # noqa: F841
        return json.dumps(self.msg).encode()


# Not including [MockGitHubAPIResponse] type argument to maintain compatibility with Python <= 3.8
def mock_urlopen(github_api_state: MockGitHubAPIState) -> Callable[[Request], AbstractContextManager]:  # type: ignore[type-arg]
    @contextmanager
    def inner(request: Request) -> Iterator[MockGitHubAPIResponse]:
        yield __mock_urlopen_impl(github_api_state, request)
    return inner


def __mock_urlopen_impl(github_api_state: MockGitHubAPIState, request: Request) -> MockGitHubAPIResponse:

    parsed_url: ParseResult = urlparse(request.full_url)
    url_segments: List[str] = [s for s in parsed_url.path.split('/') if s]
    query_params: Dict[str, str] = {k: v[0] for k, v in parse_qs(parsed_url.query).items()}
    json_data: Dict[str, Any] = request.data and json.loads(request.data)  # type: ignore

    def handle_method() -> "MockGitHubAPIResponse":
        if request.method == "GET":
            return handle_get()
        elif request.method == "PATCH":
            return handle_patch()
        elif request.method == "POST":
            return handle_post()
        else:
            return MockGitHubAPIResponse(HTTPStatus.METHOD_NOT_ALLOWED, [])

    def url_path_matches(pattern: str) -> bool:
        regex = pattern.replace('*', '[^/]+')
        return re.match('^(/api/v3)?' + regex + '$', parsed_url.path) is not None

    def handle_get() -> "MockGitHubAPIResponse":
        if url_path_matches('/search/issues'):
            parsed_query: Dict[str, List[str]] = defaultdict(list)
            for kv in query_params['q'].split():
                k, v = kv.split(':', 1)
                parsed_query[k].append(v)
            if parsed_query.get("author"):
                return MockGitHubAPISearchResponse(github_api_state.get_open_pulls_for_authors(parsed_query.get("author")))
            elif parsed_query.get("head"):
                return MockGitHubAPISearchResponse(github_api_state.get_open_pulls_for_heads(parsed_query.get("head")))
            else:
                raise Exception(f"Unexpected search query: {query_params['q']}")
        elif url_path_matches('/repositories/[0-9]+'):
            return MockGitHubAPIResponse(HTTPStatus.OK, {"full_name": "example-org/example-repo"})
        elif url_path_matches('/repos/*/*/pulls'):
            full_head_name: Optional[str] = query_params.get('head')
            if full_head_name:
                head: str = full_head_name.split(':')[1]
                prs = github_api_state.get_open_pulls_for_heads([head])
                # If no matching PRs are found, the real GitHub returns 200 OK with an empty JSON array - not 404.
                return MockGitHubAPIResponse(HTTPStatus.OK, prs)
            else:
                pulls = github_api_state.get_open_pulls()
                page_str = query_params.get('page')
                page = int(page_str) if page_str else 1
                per_page = int(query_params['per_page'])
                start = (page - 1) * per_page
                end = page * per_page
                if end < len(pulls):
                    new_query_params: Dict[str, Any] = {**query_params, 'page': page + 1}
                    new_query_string: str = urlencode(new_query_params)
                    new_url: str = parsed_url._replace(query=new_query_string).geturl()
                    link_header = f'<{new_url}>; rel="next"'
                else:
                    link_header = ''
                return MockGitHubAPIResponse(HTTPStatus.OK, response_data=pulls[start:end], headers={'link': link_header})
        elif url_path_matches('/repos/*/*/pulls/[0-9]+'):
            number = url_segments[-1]
            prs_ = github_api_state.get_pull_by_number(number)
            if prs_:
                return MockGitHubAPIResponse(HTTPStatus.OK, prs_)
            raise error_404()
        elif url_path_matches('/user'):
            return MockGitHubAPIResponse(HTTPStatus.OK, {'login': 'github_user', 'type': 'User', 'company': 'VirtusLab'})
        else:
            raise error_404()

    def handle_patch() -> "MockGitHubAPIResponse":
        assert not query_params
        if url_path_matches("/repos/*/*/(pulls|issues)/[0-9]+"):
            return update_pull_request()
        elif url_path_matches("/repositories/[0-9]+/(pulls|issues)/[0-9]+"):
            return update_pull_request()
        else:
            raise error_404()

    def handle_post() -> "MockGitHubAPIResponse":
        assert not query_params
        if url_path_matches("/repos/*/*/pulls"):
            head = json_data['head']
            base = json_data['base']
            if github_api_state.get_pull_by_head_and_base(head, base) is not None:
                raise error_422({'message': 'Validation Failed', 'errors': [
                    {'message': f'A pull request already exists for test_repo:{head}.'}]})
            return create_pull_request()
        elif url_path_matches("/repos/*/*/(pulls|issues)/[0-9]+/*"):
            pull_no = url_segments[-2]  # e.g. /repos/example-org/example-repo/pulls/5/requested_reviewers
            pull = github_api_state.get_pull_by_number(pull_no)
            assert pull is not None
            if "invalid-user" in list(json_data.values())[0]:
                raise error_422(
                    {"message": "Reviews may only be requested from collaborators. "
                                "One or more of the users or teams you specified is not a collaborator "
                                "of the example-org/example-repo repository."})
            else:
                fill_pull_request_from_json_data(pull)
                return MockGitHubAPIResponse(HTTPStatus.OK, pull)
        else:
            raise error_404()

    def update_pull_request() -> "MockGitHubAPIResponse":
        pull_no = url_segments[-1]
        pull = github_api_state.get_pull_by_number(pull_no)
        assert pull is not None
        fill_pull_request_from_json_data(pull)
        return MockGitHubAPIResponse(HTTPStatus.OK, pull)

    def create_pull_request() -> "MockGitHubAPIResponse":
        pull = {'user': {'login': 'some_other_user'},
                'html_url': 'www.github.com',
                'state': 'open',
                'head': {'ref': "", 'repo': {'full_name': 'testing:checkout_prs', 'html_url': 'https:/example.org/pull/1234'}},
                'base': {'ref': ""}}
        fill_pull_request_from_json_data(pull)
        github_api_state.add_pull(pull)
        return MockGitHubAPIResponse(HTTPStatus.CREATED, pull)

    def fill_pull_request_from_json_data(pull: Dict[str, Any]) -> None:
        for key in json_data.keys():
            value = json_data[key]
            if key in ('base', 'head'):
                pull[key]['ref'] = value
            else:
                pull[key] = value

    def redirect_307(location: str) -> HTTPError:
        return HTTPError(parsed_url.hostname, 307, 'Temporary redirect', {'Location': location}, None)  # type: ignore[arg-type]

    def error_404() -> HTTPError:
        return HTTPError(parsed_url.hostname, 404, 'Not found', None, None)  # type: ignore[arg-type]

    def error_422(response_data: Any) -> MockHTTPError:
        return MockHTTPError(parsed_url.hostname, 422, response_data, None, None)  # type: ignore[arg-type]

    if parsed_url.hostname == "403.example.org":
        raise HTTPError("http://example.org", 403, 'Forbidden', None, None)  # type: ignore[arg-type]

    if request.method != "GET" and url_segments[:3] == ["repos", "example-org", "old-example-repo"]:
        original_path = parsed_url.path
        new_path = original_path.replace("/repos/example-org/old-example-repo", "/repositories/123456789")
        location = parsed_url._replace(path=new_path).geturl()
        raise redirect_307(location)

    return handle_method()
