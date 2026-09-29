# Schema drift

The alert names added and widened columns. Its run event includes the table and schema
fingerprint. Landing continues for compatible changes. Incompatible types or removed
required columns fail as `schema_breaking`.

Deploy computes expected fingerprints from current typed declarations and checked-in
fixture schemas. Those fingerprints are acknowledged before the new code lands rows.
Acknowledgements match source, table, column and type. A different table or type
still alerts. If the check cannot run, bootstrap warns and continues.

Compare the changed columns with the archived schema and the provider response fixture.
Update the declaration and fixture when the new shape is intended. Regenerate source
artifacts. Use the function page's drift acknowledgement only after checking the columns.
