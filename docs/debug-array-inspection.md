# Inspect business collections in Dev debug inputs

The existing [frozen inputs endpoint](debug-input-inspection.md) can now project homogeneous collections such as requisition line items. No new route or provider setup is needed.

Opt in explicitly at every array layer:

```json
{
  "type": "array",
  "x-nexusflow-inspect": true,
  "x-nexusflow-inspect-array": true,
  "items": {
    "type": "object",
    "properties": {
      "sku": {"type": "string", "x-nexusflow-inspect": true},
      "quantity": {"type": "integer", "x-nexusflow-inspect": true},
      "vendor_token": {"type": "string", "writeOnly": true}
    }
  }
}
```

Only public fields of each item appear. Unknown keys and unclassified/protected children remain hidden. Primitive items need their own public annotation; nested arrays need both array markers at each layer. Array annotations do not override protected parent/item flags. Missing/unsupported item definitions, tuple/containment rules, references and compositions keep the whole array hidden.

The new array marker is deliberate: old arrays marked only with `x-nexusflow-inspect` remain redacted after an upgrade. Create a new project revision and debug session to opt in; edits do not declassify old sessions.

Positions are preserved. A disclosed item that exceeds a scalar/type/depth limit can be represented as null with its index in `redacted_paths`, rather than shifting later items. Ordinary public null values have no such marker. Protected or unsupported homogeneous item declarations hide the whole array before disclosing its count.

At most 100 leading items per array are returned. Omitted tails set `truncated` and mark the array path. The existing global response/depth/traversal/redaction limits still apply, including the 16 KiB response ceiling. Inspection changes no workflow gate or activation and remains available while its worker is stopped or after completion.

[Enable the local debugger](local-development.md#enable-dev-debugging), then create [the line-item sample project](../examples/local-debug-array-project.json) through POST /projects. Start workflow `inspect-line-items` at revision 1 with configuration `local-v1`, explicit real mode, and input such as:

```json
{
  "requisition_id": "REQ-1",
  "line_items": [
    {"sku": "LAPTOP", "quantity": 1, "vendor_token": "private-example"}
  ]
}
```

Read the session's `/inputs` endpoint. The requisition ID, SKU and quantity are public; the token remains hidden. This is an inspection example, not a complete purchasing workflow. Runtime outputs and the React inspector remain planned.
