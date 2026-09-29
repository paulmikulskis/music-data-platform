{% macro mdp_billboard_credit_method(credit, primary) -%}
case
    when {{ credit }} = {{ primary }} then 'folded'
    when left({{ credit }}, length({{ primary }})) = {{ primary }}
        and {{ mdp_regex_extract('substring(' ~ credit ~ ', length(' ~ primary ~ ') + 1)', "^( +(featuring|feat[.]?|with|x|and) +| *[&,] *).+") }} is not null
        then 'credit_primary'
end
{%- endmacro %}
