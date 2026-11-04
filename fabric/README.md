# Fabric items

Microsoft Fabric items in Fabric Git format, plus the settings file for the deploy script.

**Owner:** Integration. **Status:** Written and checked offline. Nobody has run any item in Fabric.

| Path | Contents |
| --- | --- |
| `items/` | One folder per item (`<name>.<Type>`) with a `.platform` file. Fabric Git integration and fabric-cicd read this layout. |
| `config/fabric.config.example.yml` | Settings for the deploy script. Copy it to `fabric.config.yml`. Git ignores that copy. |

The semantic model and the report stay in `../reports/`. The deploy script stages them together with `items/`.

## Generated files

Do not edit these by hand. Change the source and run `python scripts/fabric/build_fabric_items.py`.

| Generated | Source |
| --- | --- |
| `items/*.Notebook/` | `notebooks/*.ipynb` |
| `items/HomeBridgeEventhouse.Eventhouse/.children/HomeBridgeEvents.KQLDatabase/DatabaseSchema.kql` | `kql/*.kql` |

`python scripts/fabric/build_fabric_items.py --check` exits with 1 when a generated file is out of date. The tests run it.

## Rules

- A `logicalId` in a `.platform` file is derived from the item name (`scripts/fabric/hbfabric/ids.py`). Do not change one by hand. Files point to other items by logical ID.
- The default workspace ID `00000000-0000-0000-0000-000000000000` in a file means "the target workspace". fabric-cicd replaces it on deploy.
- Never put a connection string, key, token, tenant ID or user name in an item file. The offline check looks for common patterns.
- Synthetic data only.

See [docs/fabric-deployment.md](../docs/fabric-deployment.md) for the steps and the limits.
