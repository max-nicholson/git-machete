GITHUB_ANNOTATE_WITH_URLS = 'machete.github.annotateWithUrls'
GITHUB_FORCE_DESCRIPTION_FROM_COMMIT_MESSAGE = 'machete.github.forceDescriptionFromCommitMessage'
GITHUB_DOMAIN = 'machete.github.domain'
GITHUB_REMOTE = 'machete.github.remote'
GITHUB_ORGANIZATION = 'machete.github.organization'
GITHUB_REPOSITORY = 'machete.github.repository'

GITLAB_PROJECT_ID = 'machete.gitlab.projectId'
GITLAB_PROJECT_PATH = 'machete.gitlab.projectPath'
GITLAB_NAMESPACE = 'machete.gitlab.namespace'

STATUS_EXTRA_SPACE_BEFORE_BRANCH_NAME = 'machete.status.extraSpaceBeforeBranchName'
TRAVERSE_PUSH = 'machete.traverse.push'
WORKTREE_USE_TOP_LEVEL_MACHETE_FILE = 'machete.worktree.useTopLevelMacheteFile'

def domain_for(platform: str) -> str:
    return f'machete.{platform.lower()}.domain'


def force_description_from_commit_message_for(platform: str) -> str:
    return f'machete.{platform.lower()}.forceDescriptionFromCommitMessage'


def override_fork_point_to(branch: str) -> str:
    return f'machete.overrideForkPoint.{branch}.to'


def override_fork_point_while_descendant_of(branch: str) -> str:
    return f'machete.overrideForkPoint.{branch}.whileDescendantOf'
