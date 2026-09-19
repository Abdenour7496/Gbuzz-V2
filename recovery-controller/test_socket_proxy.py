import unittest
from unittest.mock import Mock
from socket_proxy import Boundary, sanitized

class BoundaryTests(unittest.TestCase):
    def setUp(self):
        self.docker=Mock();self.boundary=Boundary(self.docker,'gbuzz')
        self.row={'Id':'a'*64,'Config':{'Env':['SECRET=hidden'],'Labels':{
            'com.gbuzz.recovery.enabled':'true','com.gbuzz.recovery.stack':'gbuzz','com.gbuzz.recovery.action':'restart'}}}
        self.docker.request.return_value=self.row
    def test_forbidden_operations_never_reach_docker(self):
        for method,path in [('POST','/containers/create'),('POST','/containers/'+'a'*64+'/exec'),('GET','/images/json'),('GET','/containers/'+'a'*64+'/archive')]:
            self.assertEqual(self.boundary.dispatch(method,path)[0],403)
        self.docker.request.assert_not_called()
    def test_restart_checks_labels(self):
        path='/containers/'+'a'*64+'/restart'
        self.assertEqual(self.boundary.dispatch('POST',path)[0],200)
        self.docker.restart.assert_called_once_with('a'*64,20)
        self.row['Config']['Labels']['com.gbuzz.recovery.action']='monitor'
        self.assertEqual(self.boundary.dispatch('POST',path)[0],403)
        self.assertEqual(self.docker.restart.call_count,1)
    def test_inspection_hides_secrets(self):
        self.assertNotIn('Env',sanitized(self.row)['Config'])
        self.row['Config']['Labels']['com.gbuzz.recovery.stack']='other'
        self.assertEqual(self.boundary.dispatch('GET','/containers/'+'a'*64+'/json')[0],403)
