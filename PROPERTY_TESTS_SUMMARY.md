# Property-Based Tests Summary

This document summarizes all new property-based tests created for the Guardian core modules using Hypothesis.

## Test Files Created

### 1. `tests/property/test_version_properties.py`
**Module**: `guardian_core.version`

**Public Functions**:
- `get_version() -> str`
- `get_git_sha() -> str`

**Invariants Tested**:
1. ✓ `get_version()` always returns the constant version string "0.1.0"
2. ✓ `get_version()` is deterministic (returns same value on multiple calls)
3. ✓ `get_version()` matches the version in pyproject.toml
4. ✓ `get_git_sha()` returns either an env-provided SHA or "unknown" fallback
5. ✓ `get_git_sha()` is deterministic (returns same value on multiple calls)
6. ✓ `get_git_sha()` respects GUARDIAN_GIT_SHA environment variable
7. ✓ Version string follows semantic versioning format (major.minor.patch)
8. ✓ Both functions return non-empty strings

**Test Classes**: 3
- `TestGetVersion` (4 tests)
- `TestGetGitSha` (4 tests)
- `TestVersionAuxiliaries` (3 tests)

---

### 2. `tests/property/test_redis_client_properties.py`
**Module**: `guardian_core.redis_client`

**Public Functions**:
- `get_redis_url() -> str`
- `make_redis_client(url: str | None = None) -> redis.Redis[bytes]`
- `ping_redis(client: redis.Redis[bytes] | None = None) -> bool`

**Invariants Tested**:
1. ✓ `get_redis_url()` always returns a non-empty string
2. ✓ `get_redis_url()` returns a valid Redis URL (starts with redis://)
3. ✓ `get_redis_url()` defaults to redis://localhost:6379/0
4. ✓ `get_redis_url()` respects REDIS_URL environment variable
5. ✓ `get_redis_url()` is deterministic
6. ✓ `make_redis_client()` returns a redis.Redis instance
7. ✓ `make_redis_client()` sets socket_timeout to 1.0
8. ✓ `make_redis_client()` sets socket_connect_timeout to 1.0
9. ✓ `make_redis_client()` supports database selection (0-15)
10. ✓ `ping_redis()` always returns a boolean
11. ✓ `ping_redis()` returns False on connection errors
12. ✓ `ping_redis()` returns False on general exceptions
13. ✓ `ping_redis()` returns True when ping succeeds
14. ✓ `ping_redis()` handles byte responses correctly
15. ✓ `ping_redis()` uses default client when none provided
16. ✓ `ping_redis()` with provided client uses it (verified with mocks)

**Test Classes**: 4
- `TestGetRedisUrl` (5 tests)
- `TestMakeRedisClient` (5 tests)
- `TestPingRedis` (7 tests)
- `TestRedisClientIntegration` (3 tests)

---

### 3. `tests/property/test_db_properties.py`
**Module**: `guardian_core.db`

**Public Functions**:
- `get_database_url() -> str`
- `make_engine(url: str | None = None) -> Engine`
- `get_engine() -> Engine`
- `get_sessionmaker() -> sessionmaker[Session]`
- `reset_engine() -> None`
- `session_scope() -> Iterator[Session]` (context manager)

**Invariants Tested**:
1. ✓ `get_database_url()` returns a valid SQLAlchemy connection string
2. ✓ `get_database_url()` respects DATABASE_URL environment variable
3. ✓ `get_database_url()` defaults to SQLite
4. ✓ `get_database_url()` is deterministic
5. ✓ `make_engine()` creates a valid SQLAlchemy Engine
6. ✓ `make_engine()` with SQLite disables check_same_thread
7. ✓ `make_engine()` enables future mode (SQLAlchemy 2.0 behavior)
8. ✓ `get_engine()` returns an Engine instance
9. ✓ `get_engine()` is a singleton (returns same instance on multiple calls)
10. ✓ `get_sessionmaker()` returns a sessionmaker instance
11. ✓ `get_sessionmaker()` is a singleton (cached)
12. ✓ `reset_engine()` clears the singleton cache
13. ✓ `reset_engine()` disposes existing engine
14. ✓ `session_scope()` yields a Session instance
15. ✓ `session_scope()` commits on successful exit
16. ✓ `session_scope()` rolls back on exception
17. ✓ `session_scope()` closes session in finally block
18. ✓ `session_scope()` re-raises exceptions
19. ✓ `session_scope()` can be used multiple times in sequence
20. ✓ `session_scope()` sets autoflush=False
21. ✓ `session_scope()` sets expire_on_commit=False

**Test Classes**: 5
- `TestGetDatabaseUrl` (4 tests)
- `TestMakeEngine` (6 tests)
- `TestEngineSingleton` (7 tests)
- `TestSessionScope` (8 tests)
- `TestDatabaseIntegration` (3 tests)

---

### 4. `tests/property/test_models_properties.py`
**Module**: `guardian_core.models`

**ORM Models**:
- `Service`
- `Contract`
- `ContractVersion`
- `Client`
- `Endpoint`
- `Usage`
- `Deprecation`

**Invariants Tested**:
1. ✓ Service ID is automatically generated as UUID7 string
2. ✓ Client ID is automatically generated as UUID7 string
3. ✓ ContractVersion ID is automatically generated as UUID7 string
4. ✓ Service created_at is set to current UTC time
5. ✓ Client created_at is set to current UTC time
6. ✓ Service names are globally unique (constraint)
7. ✓ Contract names are unique per service (composite constraint)
8. ✓ Same contract name allowed in different services
9. ✓ Client names are globally unique (constraint)
10. ✓ ContractVersion spec_metadata accepts JSON-serializable dicts
11. ✓ Deprecation notes accepts JSON-serializable dicts
12. ✓ Deprecation reason can be None (optional)
13. ✓ Endpoint operation_id can be None (optional)
14. ✓ Deprecation status defaults to 'proposed'
15. ✓ Usage source defaults to 'manual'
16. ✓ Usage request_count defaults to 0

**Test Classes**: 6
- `TestModelIdGeneration` (3 tests)
- `TestModelTimestamps` (2 tests)
- `TestModelConstraints` (5 tests)
- `TestModelJsonColumns` (2 tests)
- `TestModelOptionalFields` (2 tests)
- `TestModelDefaults` (4 tests)

---

## Test Statistics

| Module | Tests | Test Classes | Strategies |
|--------|-------|--------------|-----------|
| version | 11 | 3 | Basic property tests |
| redis_client | 20 | 4 | URL patterns, DB numbers, mock clients |
| db | 28 | 5 | DB URL patterns, session management |
| models | 18 | 6 | Valid names, metadata dicts, timestamps |
| **Total** | **77** | **18** | - |

## Property Test Patterns Used

1. **Determinism**: Functions called multiple times produce same output
2. **Environment Variable Handling**: Respects env vars with sensible defaults
3. **Type Correctness**: Return values have expected types
4. **Constraint Validation**: Database constraints properly enforced
5. **Default Values**: Models and functions have correct defaults
6. **Optional Fields**: None values properly handled
7. **Context Manager**: session_scope() properly commits/rolls back
8. **Singleton Pattern**: get_engine() and get_sessionmaker() are cached
9. **Integration**: Functions work together correctly

## Hypothesis Strategies Used

- `st.text()` - For strings with size/alphabet constraints
- `st.integers()` - For numeric database selection
- `st.just()` - For literal values (e.g., constant versions)
- `st.one_of()` - For multiple value types
- `st.dictionaries()` - For JSON-serializable metadata
- `st.none()` - For optional fields
- `.filter()` - For refinement (e.g., redis:// URLs only)
- `.map()` - For transformations (e.g., base64 encoding)

## Running the Tests

To run all new property tests:
```bash
python -m pytest tests/property/test_version_properties.py -v
python -m pytest tests/property/test_redis_client_properties.py -v
python -m pytest tests/property/test_db_properties.py -v
python -m pytest tests/property/test_models_properties.py -v
```

Or run all property tests together:
```bash
python -m pytest tests/property/ -q
```

## Requirements

All tests use:
- `pytest` >= 8.0
- `hypothesis` >= 6.100
- All production dependencies (sqlalchemy, redis, pydantic, etc.)

## Coverage

These property tests provide comprehensive coverage of:
- ✓ `guardian_core.version` public API (100%)
- ✓ `guardian_core.redis_client` public API (100%)
- ✓ `guardian_core.db` public API (100%)
- ✓ `guardian_core.models` ORM behavior (core invariants)

The tests focus on invariants and behavioral properties rather than just happy-path unit tests, ensuring robust validation across a wide range of inputs through Hypothesis's example generation.
