from mdp_functions.layers import gold


@gold(
    source_key="adversarial_gold",
    reads=["marts.input"],
    writes=["raw.adversarial_gold"],
    cadence="daily",
)
async def invalid(ctx, rows):
    yield {"value": 1}
