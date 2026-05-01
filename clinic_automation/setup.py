from setuptools import find_packages, setup

subpackages = find_packages(exclude=("tests", "tests.*"))
packages = ["clinic_automation"] + [
    f"clinic_automation.{package_name}" for package_name in subpackages
]

setup(
    name="clinic-automation",
    version="1.0.0",
    description="Çocuk ve Ergen Psikiyatrisi Polikliniği Otomasyon Sistemi",
    packages=packages,
    package_dir={"clinic_automation": "."},
    python_requires=">=3.11",
    entry_points={
        "console_scripts": [
            "clinic=clinic_automation.main:cli",
        ],
    },
)
