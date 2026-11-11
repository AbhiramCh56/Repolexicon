import uuid
from contextlib import nullcontext
from types import SimpleNamespace

# Importing every model registers the SQLAlchemy mappers so relationship
# strings such as Repository.owner -> "User" can be resolved in tests.
import app.models.user  # noqa: F401
import app.models.chat  # noqa: F401
from app.models.file import File
from app.models.repository import Repository


class FakeQuery:
    def __init__(self, result=None, rows=None):
        self.result = result
        self.rows = rows if rows is not None else []

    def filter(self, *args, **kwargs):
        return self

    def join(self, *args, **kwargs):
        return self

    def outerjoin(self, *args, **kwargs):
        return self

    def group_by(self, *args, **kwargs):
        return self

    def limit(self, n):
        # Kept faithful: a real query returns at most n rows.
        self._limit = n
        return self

    def order_by(self, *args, **kwargs):
        return self

    def delete(self):
        return 0

    def first(self):
        return self.result

    def one_or_none(self):
        return self.result

    def all(self):
        limit = getattr(self, "_limit", None)
        if limit is not None:
            return self.rows[:limit]
        return self.rows


class FakeSession:
    def __init__(self, repository=None, rows=None, query_results=None, execute_rows=None):
        self.repository = repository
        self.rows = rows if rows is not None else []
        # When provided, each query() call consumes the next entry instead of
        # returning self.rows. Lets a single fake answer several distinct
        # queries (e.g. per-file chunk counts, then file rows).
        self.query_results = list(query_results) if query_results else None
        self.execute_rows = execute_rows if execute_rows is not None else []
        self.added = []
        self.commits = 0
        self.rollbacks = 0
        self.needs_id = []

    def query(self, *args):
        if self.query_results:
            return FakeQuery(self.repository, rows=self.query_results.pop(0))
        return FakeQuery(self.repository, self.rows)

    def execute(self, *args, **kwargs):
        return SimpleNamespace(all=lambda: self.execute_rows)

    def add(self, obj):
        self.added.append(obj)
        self.needs_id.append(obj)

    def add_all(self, objs):
        for obj in objs:
            self.added.append(obj)

    def flush(self):
        for obj in self.needs_id:
            if getattr(obj, "id", None) is None:
                obj.id = uuid.uuid4()
        self.needs_id.clear()

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def refresh(self, obj):
        return None

    def begin_nested(self):
        return nullcontext()

    def files(self):
        return [o for o in self.added if isinstance(o, File)]


def make_repository(local_path=None, status="completed", **kwargs):
    values = {
        "id": uuid.uuid4(),
        "full_name": "acme/demo",
        "github_url": "https://github.com/acme/demo",
        "name": "demo",
        "status": status,
        "local_path": local_path,
    }
    values.update(kwargs)
    return Repository(**values)


class FakeCollection:
    def __init__(self, name, fail_on_add_calls=()):
        self.name = name
        self.documents = []
        self.metadatas = []
        self.ids = []
        self.add_calls = 0
        self.fail_on_add_calls = set(fail_on_add_calls)

    def add(self, documents, embeddings, metadatas, ids):
        self.add_calls += 1
        if self.add_calls in self.fail_on_add_calls:
            raise RuntimeError(f"simulated failure on add call {self.add_calls}")
        self.documents.extend(documents)
        self.metadatas.extend(metadatas)
        self.ids.extend(ids)

    def count(self):
        return len(self.ids)


class FakeChromaClient:
    def __init__(self):
        self.collections = {}
        self.deleted = []

    def get_or_create_collection(self, name, metadata=None):
        if name not in self.collections:
            self.collections[name] = FakeCollection(name)
        return self.collections[name]

    def get_collection(self, name):
        if name not in self.collections:
            raise ValueError(f"collection {name} not found")
        return self.collections[name]

    def delete_collection(self, name):
        if name not in self.collections:
            raise ValueError(f"collection {name} not found")
        del self.collections[name]
        self.deleted.append(name)
        return True


class FakeEmbedder:
    def __init__(self, fail_on_call=None):
        self.calls = 0
        self.fail_on_call = fail_on_call

    def embed_documents(self, documents):
        self.calls += 1
        if self.fail_on_call is not None and self.calls == self.fail_on_call:
            raise RuntimeError("simulated embedding failure")
        return [[0.1] * 8 for _ in documents]

    def embed_query(self, text):
        return [0.1] * 8
