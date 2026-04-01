from setuptools import setup, find_packages

setup(
    name="clinic-automation",
    version="1.0.0",
    description="Çocuk ve Ergen Psikiyatrisi Polikliniği Otomasyon Sistemi",
    packages=find_packages(),
    python_requires=">=3.11",
    entry_points={
        "console_scripts": [
            "clinic=clinic_automation.main:cli",
        ],
    },
)
