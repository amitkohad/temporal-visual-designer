# Portable project packages

A published project release can be exported as a whole-project ZIP and inspected offline through the authenticated API. Export preserves the release snapshot, including workflows, forms, declarations, layout, optional authored test definitions and exact published Python bundles. It does not package current draft edits, live environment settings, execution state or deployment evidence.

Using your configured organization/environment API base and signed headers, request:

```text
GET /projects/{project_id}/releases/{release_id}/package
```

This requires fresh `package.export`. The response is a private `application/zip` attachment named `nexus-flow-project.zip` with no-store, nosniff and an `X-Package-Digest` header. Save it as a file. Missing or unavailable dependencies fail the whole export; no partial package is returned. Deprecated pinned artifacts remain readable. This exporter rejects withdrawn artifacts, including artifacts retained elsewhere; server-owned retained-export orchestration remains future work.

To inspect a package, send its raw bytes to:

```text
POST /packages/validation
Content-Type: application/zip
```

Validation requires fresh `package.import` in the target scope and a matching organization declaration. For example, a business author with Dev export and UAT import permissions can validate the Dev package in UAT. Prod retains its existing production-support policy. The route returns a typed manifest describing the project/release, source revision/time, pinned activities, file inventory and checksums. It does not save or activate the package.

The archive contains only these paths:

- `manifest.json`: versioned package metadata and checksums.
- `project.json`: the complete authored project snapshot.
- `activities/{artifact-hex}.zip`: each exact pinned Python source bundle, including its declared vendored files.

The manifest's package digest binds its canonical contents excluding the digest field itself. Repacking only outer ZIP timestamps/order/compression preserves logical identity. Changing indexed bytes, references or project resources requires matching inventory and project/executable digests. Checksums detect inconsistent content; they are not publisher signatures or approval verification.

Default codec/export bounds are 16 MiB ZIP, 32 MiB outer expansion, 8 MiB per indexed file, 4,000,000 project bytes, 256 KiB manifest, 100 activities and 32 MiB aggregate nested source files. The API's existing 6,000,000-byte request ceiling is smaller, so some valid exported packages require a host with an explicitly configured larger upload ceiling for HTTP inspection. Other codec and nested source-bundle limits still apply.

Inspection rejects unsafe archive shapes, duplicate JSON, invalid baseline project structure and damaged/missing content. It never extracts or executes Python. It checks neither target worker readiness nor production trust/configuration; it grants no runtime access. Source-authored strings remain authored data, subject to the baseline publication/bundle checks rather than an automatic DLP guarantee. For durable target storage, use [package import staging](package-import-staging.md). Executable release/artifact materialization, configuration binding, provenance policy and Admin UI remain outstanding.
