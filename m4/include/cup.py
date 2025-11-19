@task
def cup(basedir):
    """
    The cup for the tea.
    """
    path = Path(basedir) / "cup"
    taskname = "The cup"
    yield taskname
    yield Asset(path, path.exists)
    yield None
    log.info("%s: Getting cup", taskname)
    path.mkdir(parents=True)
