# ADR 0040: Explicitly opted-in homogeneous array inspection

Status: accepted for the bounded Dev workflow-input inspector.

Purchase requisitions and onboarding inputs commonly include collections. Extend the existing frozen-input projection with homogeneous arrays while preserving the privacy of previously unsupported schemas. No API, persistence, worker or start-plan format changes.

## Compatibility and classification

Every array layer must declare both `x-nexusflow-inspect: true` and the new `x-nexusflow-inspect-array: true`. Old schemas with only the original public flag remain redacted. Authors opt in through a new immutable workflow revision; the original-schema digest check prevents later edits from changing an old session's visibility.

An array requires one typed `items` schema. Primitive items require their own public flag. Object items traverse declared properties while disclosing only independently public leaves. Nested arrays need both markers at every layer. Array opt-in grants bounded structural positions/count, not blanket access to descendants. Protected parent/item flags and password format take precedence. Missing, unclassified or unsupported item schemas hide the whole array before exposing structure.

Tuple schemas, containment selectors, additional/unevaluated item rules, references, compositions and conditional schemas remain unsupported and hidden. Unknown object keys stay omitted without exposing their names. No source schema is resolved or executed.

## Positions and budgets

Preserve item positions. A classified individual value that cannot be disclosed, such as an oversized scalar or a runtime type mismatch, becomes JSON null with an indexed redaction pointer. A legitimate public null has no redaction marker. Protected object fields are omitted as before. Unsupported/protected homogeneous item declarations hide the whole array rather than publishing a placeholder list.

Each array discloses at most 100 leading items; tail exhaustion marks truncation and the array path. Global depth eight, 1,000 visited nodes, 100 redaction paths, 1,024 scalar JSON bytes and 16 KiB whole-response limits still apply. Traversal exhaustion stops the tail rather than allocating unbounded placeholders. Aggregate overflow retains the existing empty-root fallback. No reindexing of retained elements is allowed.

## Remaining scope

This extends only caller-supplied frozen workflow input inspection. Runtime variables/outputs, broader schema composition support, browser controls, cancellation/expiry, events, mocks, parallel stepping and production secret/provider integration remain open. The existing owner/Dev/source-digest checks and worker-independent read path are unchanged.
