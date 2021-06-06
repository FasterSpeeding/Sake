import os
import re
import typing

import setuptools
import types

MAIN_MODULE_NAME = "sake"
TARGET_PROJECT_NAME = "hikari-sake"


def load_meta_data():
    pattern = re.compile(r"__(?P<key>\w+)__\s=\s\"(?P<value>.+)\"")
    with open(os.path.join(MAIN_MODULE_NAME, "about.py"), "r") as file:
        code = file.read()

    groups = dict(group.groups() for group in pattern.finditer(code))
    return types.SimpleNamespace(**groups)


metadata = load_meta_data()

requires: typing.List[str] = []
dependency_links: typing.List[str] = []
with open("requirements.txt") as f:
    REQUIREMENTS = f.readlines()
    for line in REQUIREMENTS:
        if line.startswith("git+"):
            dependency_links.append(line[4:])

        else:
            requires.append(line)


with open("README.md") as f:
    README = f.read()


setuptools.setup(
    name=TARGET_PROJECT_NAME,
    url=metadata.url,
    version=metadata.version,
    packages=setuptools.find_namespace_packages(include=[f"{MAIN_MODULE_NAME}*"]),
    author=metadata.author,
    author_email=metadata.email,
    license=metadata.license,
    description="A Discord API for Python and asyncio built on good intentions.",
    long_description=README,
    long_description_content_type="text/markdown",
    include_package_data=True,
    install_requires=requires,
    dependency_links=dependency_links,
    python_requires=">=3.8.0,<3.10",
    classifiers=[
        "Development Status :: 1 - Planning",
        "License :: OSI Approved :: BSD License",
        "Intended Audience :: Developers",
        "Natural Language :: English",
        "Operating System :: OS Independent",
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: Implementation :: CPython",
        "Topic :: Communications :: Chat",
        "Topic :: Software Development :: Libraries",
        "Topic :: Software Development :: Libraries :: Python Modules",
        "Topic :: Utilities",
        "Typing :: Typed",
    ],
)
