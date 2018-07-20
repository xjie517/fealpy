from setuptools import setup

setup(name='fealpy',
      version='0.1',
      description='FEALPy: Finite Element Analysis Library in Python',
      url='http://github.com/weihuayi/fealpy',
      author='Huayi Wei',
      author_email='weihuayi@xtu.edu.cn',
      license='GNU',
      packages=['fealpy'],
      install_requires=[
          'numpy',  
          'scipy', 
          'matplotlib', 
	  'msgpack',
          'pytools',
          'meshpy',
          'pyamg' 
      ],
      zip_safe=False)
