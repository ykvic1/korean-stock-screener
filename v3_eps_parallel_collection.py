"""Bounded source readers, one checkpoint caller, and drain-before-failure."""
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
import time


def collect(choices,fetch,checkpoint,*,workers=4,deadline,clock=time.monotonic):
    if (type(workers) is not int or not 1<=workers<=4
        or type(choices) not in (list,tuple) or len(choices)>16):
        raise ValueError('invalid_parallel_eps_budget')
    iterator=iter(choices);pending={};failure=None;exhausted=False
    with ThreadPoolExecutor(max_workers=workers) as executor:
        def fill():
            nonlocal exhausted
            while not exhausted and len(pending)<workers and clock()+120<deadline:
                try:choice=next(iterator)
                except StopIteration:exhausted=True;break
                pending[executor.submit(fetch,choice)]=choice
        fill()
        while pending:
            done,_=wait(pending,return_when=FIRST_COMPLETED)
            for future in done:
                pending.pop(future)
                try:result=future.result()
                except Exception as error:
                    if failure is None:failure=error
                else:
                    if result is not None:checkpoint(result)
            # A source error stops new reads, but valid in-flight results are
            # still checked and checkpointed by this sole calling thread.
            if failure is None:fill()
    if failure is not None:raise failure
