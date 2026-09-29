# Purpose

Music Data Platform makes public music observations comparable without losing their origin or uncertainty. Charts, playlists, catalog identifiers, radio plays, and public listening statistics arrive on different schedules and use different identifiers.

The system records requests and runs, validates each record, and performs joins in SQL. Recording identity uses ISRCs and MusicBrainz relationships, retaining unresolved and conflicting evidence. A missing observation remains unknown rather than becoming a zero.

Each served row carries source keys and usage-rights flags. Serving does not silently filter a source; derivative consumers apply their own eligibility gate, where unknown permission is false.

Cycles freeze target membership and visible inputs. A close commits an ordered watermark for its scope, and the warehouse acknowledges that watermark before the close returns. Retries resume work within that cycle. Global is the default scope; tenant ids provide generic execution and access isolation.

The project aims to make collection, SQL transformations, and typed reads inspectable together. Availability and access conditions are documented per source, and offline fixtures verify behavior without claiming live coverage.
