{{ config(materialized='table', tags=['__LAYER__', 'invoke', 'cadence:__CADENCE__', 'scope:__SCOPE__'],
          pre_hook="{{ mdp_statement_timeout('__SOURCE__') }}") }}
__DEPENDENCY__
{{ mdp_invoke('__SOURCE__'__TARGET__) }}
