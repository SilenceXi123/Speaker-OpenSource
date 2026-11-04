from __future__ import annotations

import argparse
import json

from app.db.session import SessionLocal
from app.services.provider_costs import provider_cost_schema_status
from app.services.provider_price_catalog import (
    apply_official_price_catalog,
    backfill_unpriced_catalog_events,
    official_price_catalog_status,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description='Preview or apply the Speaker V2 official list-price catalog.',
    )
    parser.add_argument('--apply', action='store_true', help='append missing price versions')
    args = parser.parse_args()

    with SessionLocal() as session:
        schema = provider_cost_schema_status(session.get_bind())
        if not schema['ready']:
            print(json.dumps({'schema': schema, 'error': 'provider cost schema is not ready'}))
            return 2
        if args.apply:
            result = apply_official_price_catalog(session)
            result['backfill'] = backfill_unpriced_catalog_events(session)
        else:
            result = {'inserted_count': 0, 'status': official_price_catalog_status(session)}
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
