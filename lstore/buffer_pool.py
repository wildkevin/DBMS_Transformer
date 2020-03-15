from lstore.config import *
from lstore.page import *
import threading 
import os
import heapq
import pickle
from datetime import datetime
import time
import copy


def read_page(page_path):
    f = open(page_path, "rb")
    page = pickle.load(f)  # Load entire page object
    new_page = Page()
    new_page.from_file(page)
    f.close()
    return new_page

def write_page(page, page_path):
    # Create if not existed
    dirname = os.path.dirname(page_path)
    if not os.path.exists(dirname):
        os.makedirs(dirname)
    f = open(page_path, "wb")
    pickle.dump(page, f)  # Dump entire page object
    f.close()



class BufferPool:
    size = BUFFER_POOL_SIZE
    path = None
    # active pages loaded in bufferpool
    page_directories = {}
    # Pop the least freuqently used page
    tstamp_directories = {}
    tps = {}  # Key: (table_name, col_index, page_range_index), value: tps
    latest_tail = {}  # Key: (table_name, col_index, page_range_index), value: lastest tail page id of specified page range
    def __init__(self):
        # print("Init BufferPool. Do Nothing ...")
        pass

    @classmethod
    def initial_path(cls, path):
        cls.path = path

    @classmethod
    def add_page(cls, uid, default=True):
        if default:
            cls.page_directories[uid] = None
        else:
            cls.page_directories[uid] = Page()
            cls.page_directories[uid].dirty = 1

    @classmethod
    def is_full(cls):
        return len(cls.tstamp_directories) >= cls.size

    @classmethod
    def is_page_in_buffer(cls, uid):
        return cls.page_directories[uid] is not None

    @classmethod
    def uid_to_path(cls, uid):
        """
        Convert uid to path
        uid: tuple(table_name, based_tail, column_id, page_range_id, page_id)
        """
        t_name, base_tail, column_id, page_range_id, page_id = uid
        path = os.path.join(cls.path, t_name, base_tail, str(column_id),
                            str(page_range_id), str(page_id) + ".pkl")
        return path

    @classmethod
    def check_base_range(self, t_name, col_id, page_range_id):
        page_range_path = os.path.join(BufferPool.path, t_name, "Base", str(col_id), str(page_range_id))
        return len(os.listdir(page_range_path))

    @classmethod
    def get_page(cls, t_name, base_tail, column_id, page_range_id, page_id):
        uid = (t_name, base_tail, column_id, page_range_id, page_id)
        page_path = cls.uid_to_path(uid)
        # import pdb; pdb.set_trace()

        # Brand New Page => Not on disk
        if not os.path.isfile(page_path):
            if cls.is_full():
                cls.remove_lru_page()
            cls.add_page(uid, default=False)
            # Create File if not existed => Avoid calling add_page more than once to overwrite the Page()
            dirname = os.path.dirname(page_path)
            if not os.path.isdir(dirname):
                os.makedirs(dirname)
            f = open(page_path, "w+")
            f.close()

        # Existed Page
        else:
            # Existed Page not in buffer => Read From Disk
            if not cls.is_page_in_buffer(uid):
                if cls.is_full():
                    cls.remove_lru_page()
                cls.page_directories[uid] = read_page(page_path)
        cls.tstamp_directories[uid] = datetime.timestamp(datetime.now())
        return cls.page_directories[uid]

    @classmethod
    def remove_lru_page(cls):
        # Pop least recently used page in cache
        sorted_uids = sorted(cls.tstamp_directories,
                                key=cls.tstamp_directories.get)
        oldest_uid = sorted_uids[0]  # FIXME: More complex control needed for pinning
        temp = 0
        while cls.page_directories[oldest_uid].pinned != 0:
            temp += 1
            oldest_uid = sorted_uids[temp]
            if cls.page_directories[oldest_uid].pinned == 0:
                break
        oldest_page = cls.page_directories[oldest_uid]
        assert(oldest_page is not None)

        # Check if old_page is dirty => write back
        if oldest_page.dirty == 1:
            old_page_path = cls.uid_to_path(oldest_uid)
            write_page(oldest_page, old_page_path)

        cls.page_directories[oldest_uid] = None
        del cls.tstamp_directories[oldest_uid]

    @classmethod
    def get_record(cls, t_name, base_tail, column_id, page_range_id, page_id, record_id):
        page = cls.get_page(t_name, base_tail, column_id, page_range_id, page_id)
        record_data = page.get(record_id)
        return record_data

    @classmethod
    def get_base_page_range(cls, t_name, column_id, page_range_id):
        page_range = {}
        base_page_count = cls.check_base_range(t_name, column_id, page_range_id)

        for page_id in range(base_page_count):
            args = [t_name, "Base", column_id, page_range_id, page_id]
            page = cls.get_page(*args)
            page_range[tuple(args)] = page
        return page_range

    @classmethod
    def update_base_page_range(cls, page_range):
        for uid, new_page in page_range.items():
            # TODO: Might need to handle old_page
            old_page = cls.page_directories[uid]
            # li = [('Grades', 'Base', 7, 0, 0), ('Grades', 'Base', 8, 0, 0), ('Grades', 'Base', 9, 0, 0), ('Grades', 'Base', 10, 0, 0), ('Grades', 'Base', 11, 0, 0)]
            # if uid in li:
            #     print(int.from_bytes(old_page.get(256), byteorder='big'), int.from_bytes(new_page.get(256), byteorder='big'))
            #     import pdb; pdb.set_trace()
            cls.page_directories[uid] = new_page

    @classmethod
    def get_tps(cls, t_name, column_id, page_range_id):
        return cls.tps[t_name][(column_id, page_range_id)]

    @classmethod
    def set_tps(cls, t_name, column_id, page_range_id, value=0):
        if t_name not in cls.tps.keys():
            cls.tps[t_name] = {}
        cls.tps[t_name][(column_id, page_range_id)] = value

    @classmethod
    def copy_tps(cls, old_tps):
        cls.tps = old_tps

    @classmethod
    def init_tps(cls, t_name):
        if t_name not in cls.tps.keys():
            # print("Set tps for key {}".format(t_name))
            cls.tps[t_name] = {}

    @classmethod
    def get_latest_tail(cls, t_name, column_id, page_range_id):
        "Return Latest/Last Tail Base Index of given table, column and page range"
        tid_counter = cls.latest_tail[t_name][(column_id, page_range_id)]
        return tid_counter

    @classmethod
    def set_latest_tail(cls, t_name, column_id, page_range_id, value=0):
        cls.latest_tail[t_name][(column_id, page_range_id)] = value

    @classmethod
    def copy_latest_tail(cls, old_latest_tail):
        cls.latest_tail = old_latest_tail

    @classmethod
    def init_latest_tail(cls, t_name):
        if t_name not in cls.latest_tail.keys():
            # print("Set Lastest Tail for key {}".format(t_name))
            cls.latest_tail[t_name] = {}

    @classmethod
    def get_table_tails(cls, t_name):
        # print(cls.latest_tail[t_name].keys(), cls.latest_tail[t_name].values())
        return cls.latest_tail[t_name].keys(), cls.latest_tail[t_name].values()

    @classmethod
    def close(cls):
        active_uids = list(cls.tstamp_directories.keys())
        # import pdb; pdb.set_trace()
        while len(active_uids) > 0:
            active_uids_copy = copy.deepcopy(active_uids)
            # Loop Through Pages in Bufferpool
            for i, uid in enumerate(active_uids_copy):
                page = cls.page_directories[uid]
                # Write Back Dirty Pages
                if page.dirty and not page.pinned:
                    page_path = cls.uid_to_path(uid)
                    write_page(page, page_path)
                    active_uids.pop(active_uids.index(uid)) # TODO: Can be faster easily

                # Not Dirty => No need to write to disk => Don't Handle
                if not page.dirty:
                    active_uids.pop(active_uids.index(uid))

            # Wait until Pinned Pages are unpinned
            time.sleep(1)
