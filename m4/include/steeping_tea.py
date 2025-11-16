@task
def steeping_tea(basedir):
    """
    Pour boiling water over the tea.

    Requires tea bag in cup.
    """
    yield from ingredient(basedir, "water", "Boiling water", tea_bag)
