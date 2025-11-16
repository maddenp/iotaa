@task
def tea_bag(basedir):
    """
    Place tea bag in the cup.

    Requires box of tea bags.
    """
    the_cup = cup(basedir)
    path = the_cup.ref / "tea-bag"
    taskname = "Tea bag in cup"
    yield taskname
    yield Asset(path, path.exists)
    yield [the_cup, box_of_tea_bags(basedir)]
    log.info("%s: Adding tea bag to cup", taskname)
    path.touch()
