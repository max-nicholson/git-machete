from typing import List, Optional
from typing_extensions import Protocol, Self


class Domain(Protocol):
    DEFAULT: str

    value: str

    def __str__(self) -> str:
        return self.value


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
