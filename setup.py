from setuptools import setup, find_packages

setup(
    name='trajectory-tools',
    version='0.1.0',
    description='A Python project for data import functionality',
    packages=find_packages(where='src'),
    package_dir={'': 'src'},
    include_package_data=True,
    install_requires=[],  # Add any dependencies if needed
    classifiers=[
        'Programming Language :: Python :: 3',
        'Operating System :: OS Independent',
    ],
)
