# Materialize a staged project package

First [stage the exported project](package-import-staging.md) in the target environment. Publish any required custom activity bundles through the destination catalog administration process. A configured host can [prepare immutable destination configuration](destination-configurations.md) before materializing the target project. Materialization requires exact approved artifacts and compatible configured workers; it never installs or executes uploaded Python.

With target-scoped signed headers and `package.import`, send:

```http
POST /package-imports/uat-import-001/materializations
Content-Type: application/json

{
  "operation_id": "uat-materialize-001",
  "expected_project_revision": 0,
  "configuration_revision": "uat-config-v1",
  "runtime_version": "proof.v1"
}
```

Zero requires a new destination project. For an existing project, supply its current head revision. Success creates one whole-project revision and immutable destination release atomically. It preserves the existing project name and returns the target `release_id` and `revision`, source release/revision, package digest, original materializing actor/time and state `materialized`. The staged receipt remains `staged`.

Keep the operation ID stable when retrying. An exact committed retry returns its original receipt even after the project head changes. Changing the request or staging identity under that operation conflicts. A different operation may create another revision with a fresh expected head. Read the receipt with:

```http
GET /package-imports/uat-import-001/materializations/uat-materialize-001
```

No activation occurs. Use the returned destination release ID with the existing activation API and its separate expected activation revision. Production activation retains its support-team permissions and optional change-request evidence. Activation rechecks readiness; materialization is not a promise that a later deployment will remain compatible.

Missing or withdrawn destination artifacts, unsupported workflow structure and invalid destination configuration reject the operation before creating a target revision/release. Stale target heads conflict. Unavailable storage/providers return sanitized errors; retry the original operation after recovery. Conservative artifact holds may remain after an unsuccessful attempt.

Preserve the project database, staged-package database, catalog and existing activation/execution stores during backup. The local project repository supports materialization automatically; custom hosts need a materialization-capable project repository and explicit destination readiness providers. Browser screens, cloud-provider acceptance and automatic source-artifact publication are outside this backend increment.
