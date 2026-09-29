-- Optional joins must not multiply a mart row; its keys are the union of the rows that contributed.
with inputs as (
    select 1 as id, cast('writer' as text) as writer, cast('["input","writer"]' as text) as inputs,
        cast('["other","input"]' as text) as joined, cast('["input","other","writer"]' as text) as expected
    union all
    select 2, 'writer', null, '[]', '["writer"]'
    union all
    select 3, null, null, null, '[]'
), carried as (
    select id, expected, {{ mdp_source_keys(['writer'], ['inputs', 'joined']) }} as actual from inputs
)
select id from carried where actual <> expected
union all
select 0 where (select count(*) from carried) <> 3
