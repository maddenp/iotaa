@task
def spoon(basedir):
    """
    The spoon to stir the tea.
    """
    path = Path(basedir) / "spoon"
    taskname = "The spoon"
    yield taskname
    yield Asset(path, path.exists)
    yield None
    log.info("%s: Getting spoon", taskname)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()
