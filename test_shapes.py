import json
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch
import autopilot as a


class ShapeTests(unittest.TestCase):
    def test_relocated_nested_field_is_rejected(self):
        old=[{'input':{'state':{'player':{'x':1}},'op':'move'},'expected':{'x':2}}]
        new=[{'input':{'state':{'player':{},'x':1},'op':'move'},'expected':{'x':2}}]
        with self.assertRaisesRegex(ValueError,r'player.x is required'):
            a.validate_extension(old,new)

    def test_additive_fields_and_new_values_are_allowed(self):
        old=[{'input':{'state':{'x':1}},'expected':{'x':2}}]
        new=[{'input':{'state':{'x':3,'energy':4}},'expected':{'x':4,'energy':3}}]
        a.validate_extension(old,new)

    def test_different_operation_common_structure(self):
        schema=a.shape_schema([{'op':'a','x':1},{'op':'b','y':2}])
        self.assertEqual(schema['required'],['op'])
        a.check_shape({'op':'c','z':3},schema,'input')

    def test_numbers_and_booleans_are_not_interchangeable(self):
        with self.assertRaises(ValueError):
            a.check_shape(True,{'type':'number'},'energy')

    def test_array_shape_without_fixed_length(self):
        schema=a.shape_schema([[1,2],[3]])
        a.check_shape([],schema,'values')
        a.check_shape([5,6,7],schema,'values')
        with self.assertRaises(ValueError): a.check_shape(['bad'],schema,'values')

    def test_invalid_repair_never_reaches_evaluator(self):
        result={'choices':[{'finish_reason':'stop','message':{'content':'```python\nnot python at all !!!\n```'}}]}
        class Reply:
            def __enter__(self): return self
            def __exit__(self,*args): pass
            def read(self): return json.dumps(result).encode()
        with tempfile.TemporaryDirectory() as temp:
            with patch.object(a.urllib.request,'urlopen',return_value=Reply()):
                with self.assertRaises(SyntaxError):
                    a.focused_repair(Path(temp),{'code':'def solve(x): return x','failures':[]},[])
            self.assertFalse((Path(temp)/'repair.py').exists())

    def test_identical_repair_does_not_fake_new_candidate(self):
        code='def solve(x): return x'
        result={'choices':[{'finish_reason':'stop','message':{'content':code}}]}
        class Reply:
            def __enter__(self): return self
            def __exit__(self,*args): pass
            def read(self): return json.dumps(result).encode()
        with tempfile.TemporaryDirectory() as temp:
            with patch.object(a.urllib.request,'urlopen',return_value=Reply()):
                a.focused_repair(Path(temp),{'code':code,'failures':[]},[])
            self.assertFalse((Path(temp)/'repair.py').exists())


if __name__=='__main__': unittest.main(verbosity=2)
