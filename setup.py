from distutils.core import setup

setup(
    name='python-firstpaygateway',
    version='0.1-dev',
    packages=['firstpaygateway'],
    install_requires=['requests'],
    license='MIT',
    long_description=open('README.md').read(),
)
