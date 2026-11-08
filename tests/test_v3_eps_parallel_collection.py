import threading
import unittest
from v3_eps_parallel_collection import collect


class ParallelEPSCollectionTests(unittest.TestCase):
    def test_four_readers_are_independent_but_checkpoints_run_on_calling_thread(self):
        barrier=threading.Barrier(4);writer=threading.get_ident();calls=[];saved=[];lock=threading.Lock()
        def fetch(value):
            with lock:calls.append(threading.get_ident())
            barrier.wait(timeout=3)
            return value
        def checkpoint(value):
            self.assertEqual(threading.get_ident(),writer);saved.append(value)
        collect(list(range(4)),fetch,checkpoint,deadline=1000,clock=lambda:0)
        self.assertEqual(len(set(calls)),4);self.assertCountEqual(saved,range(4))

    def test_source_failure_drains_and_saves_other_valid_inflight_result(self):
        barrier=threading.Barrier(2);saved=[]
        def fetch(value):
            barrier.wait(timeout=3)
            if value==0:raise ConnectionError('synthetic provider outage')
            return value
        with self.assertRaises(ConnectionError):collect([0,1],fetch,saved.append,workers=2,deadline=1000,clock=lambda:0)
        self.assertEqual(saved,[1])

    def test_source_failure_never_refills_unstarted_choices(self):
        called=[]
        def fetch(value):
            called.append(value);raise ConnectionError('synthetic outage')
        with self.assertRaises(ConnectionError):collect(list(range(16)),fetch,lambda v:None,workers=1,deadline=1000,clock=lambda:0)
        self.assertEqual(called,[0])

    def test_deadline_stops_new_reads_without_discarding_inflight_checkpoints(self):
        now=[0];saved=[];barrier=threading.Barrier(2);calls=[]
        def fetch(value):
            calls.append(value);barrier.wait(timeout=3);return value
        def checkpoint(value):
            saved.append(value);now[0]=100
        collect(list(range(16)),fetch,checkpoint,workers=2,deadline=200,clock=lambda:now[0])
        self.assertCountEqual(calls,[0,1]);self.assertCountEqual(saved,[0,1])
        called=[];collect([0],called.append,lambda v:None,deadline=120,clock=lambda:0)
        self.assertEqual(called,[])

    def test_worker_and_issuer_caps_and_missing_identity_do_not_create_results(self):
        for workers in (0,5,True):
            with self.assertRaises(ValueError):collect([0],lambda v:v,lambda v:None,workers=workers,deadline=1000)
        with self.assertRaises(ValueError):collect(list(range(17)),lambda v:v,lambda v:None,deadline=1000)
        saved=[];collect([0],lambda v:None,saved.append,deadline=1000,clock=lambda:0)
        self.assertEqual(saved,[])


if __name__=='__main__':unittest.main()
