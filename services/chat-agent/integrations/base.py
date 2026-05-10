# integrations/base.py — shared data types and abstract base class.
#
# Why an abstract base class?
#   Both Jira and GitHub need to answer the same question: "give me the items
#   in this project that changed since timestamp T".  Encoding that contract
#   as an ABC lets sync.py loop over a plain dict of integrations without
#   knowing which concrete class is behind each key.  Adding a new PM tool
#   (Linear, Notion, ...) is then a matter of dropping a new file in this
#   package and registering it in main.py — nothing else changes.
#
# Why keep write-side methods on the interface now?
#   Step 7 will let the agent propose ticket updates for human approval.
#   Defining create_item / update_item here (as NotImplementedError stubs)
#   means Step 7 only has to fill them in — no interface change needed.

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class Item:
    """One normalised work item from any PM tool.

    All adapter-specific fields are available under `raw` for callers that
    need them.  The top-level fields are the smallest common subset that is
    useful for RAG ingestion (title + body → searchable text) and display
    (status, assignee, url → context in the UI).
    """
    id: str            # adapter-native identifier, e.g. "ALPHA-42" or "17"
    title: str
    body: str          # description / body text; may be empty
    status: str        # e.g. "To Do", "In Progress", "open", "closed"
    assignee: str | None
    url: str           # link back to the original item
    updated_at: str    # ISO-8601 datetime string
    raw: dict = field(default_factory=dict)  # full original payload


class PMIntegration(ABC):
    """Contract that every PM adapter must satisfy."""

    @abstractmethod
    async def fetch_items(
        self,
        external_ref: dict,
        updated_since: str | None = None,
    ) -> list[Item]:
        """Return items from the external system.

        Args:
            external_ref:  The slice of project.external_refs that belongs to
                           this adapter (e.g. {"jira_project_key": "ALPHA"}).
            updated_since: ISO-8601 datetime string.  When provided, only
                           return items updated at or after this time.  Used
                           for incremental sync so we do not re-ingest
                           unchanged tickets on every run.
        """

    async def create_item(
        self, external_ref: dict, title: str, body: str
    ) -> Item:
        # Step 7 fills this in.  Defined here so callers can call it without
        # an isinstance check once it is implemented.
        raise NotImplementedError("two-way writes are not yet implemented (Step 7)")

    async def update_item(
        self, external_ref: dict, item_id: str, **kwargs
    ) -> Item:
        raise NotImplementedError("two-way writes are not yet implemented (Step 7)")

    async def add_comment(
        self, external_ref: dict, item_id: str, body: str
    ) -> dict:
        """Post a comment on an existing item.

        Args:
            external_ref: The slice of project.external_refs for this adapter.
            item_id:      The adapter-native item identifier (e.g. "ALPHA-12" or "42").
            body:         Plain-text comment body.

        Returns:
            {id, url, created_at} from the external system.
        """
        raise NotImplementedError("add_comment is not yet implemented for this integration")
