all:
	black *.py
	isort --profile black *.py
	pylint *.py
	mypy *.py
