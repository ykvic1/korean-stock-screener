"""Actual Actions publisher shells must retain a source commit after push races."""
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import tempfile
import unittest


PUBLISHERS = {
    'v3-reported-capex-total.yml': 'Verify and publish derived observations before reporting any interruption',
    'v3-xbrl-owner-profit.yml': 'Verify and publish derived observations before reporting any interruption',
    'v3-xbrl-eps-recovery.yml': 'Verify and publish derived observations before reporting any interruption',
    'v3-xbrl-cashflow-recovery.yml': 'Verify and publish derived observations before reporting any interruption',
    'v3-operational-research-score.yml': 'Publish parallel model and append versioned observations',
    'v3-money-saved-structure-replay.yml': 'Publish source-verified saved-date replay with current issuer scores',
}


def publisher_script(workflow, step_name):
    path=Path(__file__).resolve().parents[1]/'.github/workflows'/workflow
    source=path.read_text()
    step=source.split('      - name: '+step_name+'\n',1)[1]
    block=step.split('        run: |\n',1)[1].split('\n      - name:',1)[0]
    return '\n'.join(line[10:] if line.startswith('          ') else line for line in block.splitlines())


class PublisherRetryTests(unittest.TestCase):
    def run_source_push_race(self, workflow, step_name, rejected_pushes):
        real_git=shutil.which('git')
        if real_git is None:self.skipTest('git unavailable')
        script=publisher_script(workflow,step_name)
        staged=re.search(r'^git add (.+)$',script,re.M)
        self.assertIsNotNone(staged,workflow)
        paths=shlex.split(staged.group(1))
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);remote=root/'remote.git';work=root/'work';mock=root/'bin'
            subprocess.run([real_git,'init','-q','--bare','--initial-branch=main',str(remote)],check=True)
            subprocess.run([real_git,'clone','-q',str(remote),str(work)],check=True,
                           stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            generated=[]
            for name in paths:
                path=work/name
                if name.endswith('/'):
                    path.mkdir(parents=True,exist_ok=True);path=path/'sample.json'
                else:path.parent.mkdir(parents=True,exist_ok=True)
                path.write_text('{}\n');generated.append(path)
            subprocess.run([real_git,'-C',str(work),'add','.'],check=True)
            subprocess.run([real_git,'-C',str(work),'config','user.name','Test Runner'],check=True)
            subprocess.run([real_git,'-C',str(work),'config','user.email','runner@example.test'],check=True)
            subprocess.run([real_git,'-C',str(work),'commit','-qm','baseline'],check=True)
            subprocess.run([real_git,'-C',str(work),'push','-q','origin','HEAD:main'],check=True)
            observed=generated[0];observed.write_text('{"verified": true}\n')
            counter=root/'push-count';output=root/'actions-output';mock.mkdir()
            (mock/'git').write_text(f'''#!/bin/sh
if [ "$1" = push ]; then
  n=$(cat "$MOCK_PUSH_COUNT" 2>/dev/null || printf 0)
  n=$((n + 1))
  printf '%s' "$n" > "$MOCK_PUSH_COUNT"
  if [ "$n" -le "$MOCK_REJECT_PUSHES" ]; then exit 1; fi
fi
exec "{real_git}" "$@"
''')
            (mock/'git').chmod(0o755)
            for name in ('python','gh'):
                p=mock/name;p.write_text('#!/bin/sh\nexit 0\n');p.chmod(0o755)
            env={**os.environ,'PATH':str(mock)+os.pathsep+os.environ['PATH'],
                 'MOCK_PUSH_COUNT':str(counter),'MOCK_REJECT_PUSHES':str(rejected_pushes),
                 'GITHUB_OUTPUT':str(output),'GITHUB_REPOSITORY':'local/verified-test'}
            run=subprocess.run(['bash','-c',script],cwd=work,env=env,
                               stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)
            remote_value=subprocess.run([real_git,'--git-dir',str(remote),'show',
                                         'main:'+str(observed.relative_to(work))],
                                        check=True,capture_output=True,text=True).stdout
            return run.returncode,int(counter.read_text()),output.read_text() if output.exists() else '',remote_value,run.stdout

    def test_rejected_first_push_does_not_silently_drop_published_observations(self):
        for workflow,step in PUBLISHERS.items():
            with self.subTest(workflow=workflow):
                code,count,output,remote,log=self.run_source_push_race(workflow,step,1)
                self.assertEqual(code,0,log)
                self.assertEqual(count,2,log)
                self.assertEqual(remote,'{"verified": true}\n')
                if workflow!='v3-money-saved-structure-replay.yml':
                    self.assertIn('changed=true',output)

    def test_three_rejections_fail_explicitly_for_score_publication(self):
        code,count,output,remote,log=self.run_source_push_race(
            'v3-operational-research-score.yml',PUBLISHERS['v3-operational-research-score.yml'],3)
        self.assertNotEqual(code,0,log)
        self.assertEqual(count,3)
        self.assertEqual(remote,'{}\n')
        self.assertNotIn('changed=true',output)
