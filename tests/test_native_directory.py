"""Synthetic physical-page directory graphs, independent from sequence IDs."""
import copy
import hashlib
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from engine.native_directory import read_directory,validate_directory

def _symlink_or_skip(testcase, link, target, **kw):
    """Create a symlink, or skip the test where the OS does not allow it (Windows needs Developer Mode or admin)."""
    try:
        link.symlink_to(target, **kw)
    except OSError as exc:
        testcase.skipTest(f"symlinks not permitted here: {exc.strerror}")



def _crc(data,seed):
    crc=seed
    for byte in data:
        crc ^= byte<<8
        for _ in range(8):crc=((crc<<1)^(0x1021 if crc&0x8000 else 0))&65535
    return crc


def _seal(page,number):
    struct.pack_into('<I',page,0,(_crc(page[4::2],number&65535)<<16)|_crc(page[5::2],number&65535))
    return bytes(page)


def directory_fixture(folder):
    folder.mkdir(exist_ok=True)
    (folder/'Company.1800').write_bytes(bytes(512))
    (folder/'Manager.1800').write_bytes(bytes(512))
    nodes={1:{'sequence':10,'layout':24,'previous':0,'left':0,'right':0,'next':3},
           3:{'sequence':20,'layout':34,'previous':1,'left':1,'right':4,'next':4},
           4:{'sequence':30,'layout':40,'previous':3,'left':0,'right':0,'next':0}}
    header=bytearray(512)
    struct.pack_into('<6I',header,8,4,30,3,1,4,3)
    pages=[header]+[bytearray(512) for _ in range(4)]
    for number,node in nodes.items():
        page=pages[number]
        struct.pack_into('<III',page,4,node['sequence'],1,0)
        struct.pack_into('<H',page,16,11)
        struct.pack_into('<I',page,28,node['layout'])
        struct.pack_into('<IIII',page,node['layout']+34,node['previous'],node['left'],node['right'],node['next'])
    (folder/'TranMgr.1800').write_bytes(b''.join(_seal(page,n) for n,page in enumerate(pages)))
    return bytes(header),nodes


class NativeDirectoryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        self.folder=self.root/'100124'
        self.header,self.nodes=directory_fixture(self.folder)

    def tearDown(self):self.temp.cleanup()

    def test_variable_layout_page_graph_matches_bst_and_siblings(self):
        result=read_directory(self.root,'native:100124')
        self.assertTrue(result['directory_verified'])
        self.assertEqual(result['node_count'],3)
        self.assertEqual(result['root_page'],3)
        self.assertEqual(result['first_page'],1)
        self.assertEqual(result['last_page'],4)
        self.assertEqual(result['tree_reachable'],3)
        self.assertEqual(result['sibling_reachable'],3)
        self.assertEqual(result['layouts'],{'24':1,'34':1,'40':1})
        self.assertFalse(result['financial_status_verified'])
        self.assertFalse(result['standalone_books_readable'])
        self.assertNotIn('rows',result)

    def test_all_source_files_unchanged_and_direct_root_supported(self):
        before={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in self.folder.iterdir()}
        result=read_directory(self.folder,'native:.')
        self.assertEqual(result['source_sha256'],before['TranMgr.1800'])
        self.assertEqual(before,{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in self.folder.iterdir()})

    def test_wrong_count_or_header_entry_rejected(self):
        for position,value in ((28,4),(16,20),(20,3),(24,3)):
            header=bytearray(self.header);struct.pack_into('<I',header,position,value)
            with self.assertRaises(ValueError):validate_directory(header,self.nodes)

    def test_broken_reference_and_reciprocal_links_rejected(self):
        for number,name,value in ((3,'left',99),(1,'next',4),(4,'previous',1)):
            nodes=copy.deepcopy(self.nodes);nodes[number][name]=value
            with self.assertRaises(ValueError):validate_directory(self.header,nodes)

    def test_cycles_shared_children_and_orphans_rejected(self):
        for mutation in ('cycle','shared','orphan'):
            nodes=copy.deepcopy(self.nodes)
            if mutation=='cycle':nodes[1]['left']=3
            elif mutation=='shared':nodes[3]['right']=1
            else:nodes[3]['right']=0
            with self.assertRaises(ValueError):validate_directory(self.header,nodes)

    def test_tree_global_order_and_sequence_uniqueness_rejected(self):
        for seq in (20,25,31):
            nodes=copy.deepcopy(self.nodes);nodes[1]['sequence']=seq
            with self.assertRaises(ValueError):validate_directory(self.header,nodes)
        nodes=copy.deepcopy(self.nodes);nodes[3]['left'],nodes[3]['right']=4,1
        with self.assertRaises(ValueError):validate_directory(self.header,nodes)

    def test_unsupported_layout_crc_and_truncation_fail_closed(self):
        nodes=copy.deepcopy(self.nodes);nodes[1]['layout']=99
        with self.assertRaises(ValueError):validate_directory(self.header,nodes)
        path=self.folder/'TranMgr.1800';data=bytearray(path.read_bytes());data[2*512+100]^=1;path.write_bytes(data)
        with self.assertRaisesRegex(ValueError,'checksum'):read_directory(self.root,'native:100124')
        path.write_bytes(data[:-1])
        with self.assertRaisesRegex(ValueError,'aligned'):read_directory(self.root,'native:100124')

    def test_source_changes_and_traversal_and_symlinks_rejected(self):
        import engine.native_directory as native_directory
        original=native_directory.checksum
        def changing(page,number):
            if number==1:
                with (self.folder/'TranMgr.1800').open('ab') as stream:stream.write(bytes(512))
            return original(page,number)
        with patch.object(native_directory,'checksum',side_effect=changing):
            with self.assertRaisesRegex(ValueError,'changed'):read_directory(self.root,'native:100124')
        with self.assertRaises(ValueError):read_directory(self.root,'native:../100124')
        path=self.folder/'TranMgr.1800';path.unlink();_symlink_or_skip(self,path,self.folder/'Manager.1800')
        with self.assertRaises(ValueError):read_directory(self.root,'native:100124')


if __name__=='__main__':unittest.main()
