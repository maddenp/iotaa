@collection
def a_cup_of_tea(basedir):
    """
    The cup of steeped tea with sugar, and a spoon.
    """
    yield "The perfect cup of tea"
    yield [steeped_tea_with_sugar(basedir), spoon(basedir)]
