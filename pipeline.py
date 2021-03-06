import datetime
from distutils.version import StrictVersion
import hashlib
import os
import seesaw
from seesaw.config import NumberConfigValue, realize
from seesaw.externalprocess import WgetDownload
from seesaw.item import ItemInterpolation, ItemValue
from seesaw.pipeline import Pipeline
from seesaw.project import Project
from seesaw.task import SimpleTask, LimitConcurrent
from seesaw.tracker import (GetItemFromTracker, SendDoneToTracker,
    PrepareStatsForTracker, UploadWithTracker)
from seesaw.util import find_executable
import shutil
import time


# check the seesaw version
if StrictVersion(seesaw.__version__) < StrictVersion("0.1.4"):
    raise Exception("This pipeline needs seesaw version 0.1.4 or higher.")


###########################################################################
# Find a useful Wget+Lua executable.
#
# WGET_LUA will be set to the first path that
# 1. does not crash with --version, and
# 2. prints the required version string
WGET_LUA = find_executable(
    "Wget+Lua",
    ["GNU Wget 1.14.lua.20130523-9a5c"],
    [
        "./wget-lua",
        "./wget-lua-warrior",
        "./wget-lua-local",
        "../wget-lua",
        "../../wget-lua",
        "/home/warrior/wget-lua",
        "/usr/bin/wget-lua"
    ]
)

if not WGET_LUA:
    raise Exception("No usable Wget+Lua found.")


###########################################################################
# The version number of this pipeline definition.
#
# Update this each time you make a non-cosmetic change.
# It will be added to the WARC files and reported to the tracker.
VERSION = "20131227.01"
USER_AGENT = 'Mozilla/5.0 (Windows; U; Windows NT 6.1; en-US) AppleWebKit/533.20.25 (KHTML, like Gecko) Version/5.0.4 Safari/533.20.27'
TRACKER_ID = 'ptch'
TRACKER_HOST = 'tracker.archiveteam.org'


###########################################################################
# This section defines project-specific tasks.
#
# Simple tasks (tasks that do not need any concurrency) are based on the
# SimpleTask class and have a process(item) method that is called for
# each item.
class PrepareDirectories(SimpleTask):
    def __init__(self, warc_prefix):
        SimpleTask.__init__(self, "PrepareDirectories")
        self.warc_prefix = warc_prefix

    def process(self, item):
        item_name = item["item_name"]

        # We expect a list of urls (no http:// prefix ok)
        item['url_list'] = item_name.split(',')

        for url in item['url_list']:
            item.log_output('URL: ' + url)

        # Be safe about max filename length
        truncated_item_name = hashlib.sha1(item_name).hexdigest()
        dirname = "/".join((item["data_dir"], truncated_item_name))

        if os.path.isdir(dirname):
            shutil.rmtree(dirname)

        os.makedirs(dirname)

        item["item_dir"] = dirname
        item["warc_file_base"] = "%s-%s-%s" % (self.warc_prefix,
            truncated_item_name,
            time.strftime("%Y%m%d-%H%M%S"))

        open("%(item_dir)s/%(warc_file_base)s.warc.gz" % item, "w").close()


class MoveFiles(SimpleTask):
    def __init__(self):
        SimpleTask.__init__(self, "MoveFiles")

    def process(self, item):
        os.rename("%(item_dir)s/%(warc_file_base)s.warc.gz" % item,
              "%(data_dir)s/%(warc_file_base)s.warc.gz" % item)

        shutil.rmtree("%(item_dir)s" % item)


wget_args = [
    WGET_LUA,
    "-U", USER_AGENT,
    "-nv",
    "-o", ItemInterpolation("%(item_dir)s/wget.log"),
    "--lua-script", "ptch.lua",
    "--no-check-certificate",
    "--output-document", ItemInterpolation("%(item_dir)s/wget.tmp"),
    "--truncate-output",
    "-e", "robots=off",
    "--rotate-dns",
    "--page-requisites",
    "--timeout", "60",
    "--tries", "inf",
    "--waitretry", "120",
    "--span-hosts",
    "--domains", "ptch.com,ptchcdn.com,viewptch.ptchcdn.com,site-images.ptchcdn.com,site-nav.ptchcdn.com,assets.ptchcdn.com",
    "--warc-file", ItemInterpolation("%(item_dir)s/%(warc_file_base)s"),
    "--warc-header", "operator: Archive Team",
    "--warc-header", "ptch-dld-script-version: " + VERSION,
    "--warc-header", ItemInterpolation("wretch-user: %(item_name)s"),
]

if 'bind_address' in globals():
    wget_args.extend(['--bind-address', globals()['bind_address']])
    print('')
    print('*** Wget will bind address at {0} ***'.format(globals()['bind_address']))
    print('')


class WgetArgFactory(object):
    def realize(self, item):
        return realize(wget_args, item) + item['url_list']


###########################################################################
# Initialize the project.
#
# This will be shown in the warrior management panel. The logo should not
# be too big. The deadline is optional.
project = Project(
    title="Ptch",
    project_html="""
    <img class="project-logo" alt="" src="http://archiveteam.org/images/7/76/Archiveteam1.png" height="50" />
    <h2>Ptch <span class="links"><a href="http://www.ptch.com/">Website</a> &middot; <a href="http://%s/%s/">Leaderboard</a></span></h2>
    <p><b>Ptch</b> is slaughtered by Yahoo!.</p>
    """ % (TRACKER_HOST, TRACKER_ID)
    , utc_deadline=datetime.datetime(2014, 01, 02, 00, 00, 1)
)

pipeline = Pipeline(
    GetItemFromTracker("http://%s/%s" % (TRACKER_HOST, TRACKER_ID), downloader,
        VERSION),
    PrepareDirectories(warc_prefix="ptch"),
    WgetDownload(
        WgetArgFactory(),
        max_tries=5,
        accept_on_exit_code=[0, 8],
    ),
    PrepareStatsForTracker(
        defaults={ "downloader": downloader, "version": VERSION },
        file_groups={
            "data": [ ItemInterpolation("%(item_dir)s/%(warc_file_base)s.warc.gz") ]
            }
    ),
    MoveFiles(),
    LimitConcurrent(NumberConfigValue(min=1, max=4, default="1",
        name="shared:rsync_threads", title="Rsync threads",
        description="The maximum number of concurrent uploads."),
        UploadWithTracker(
            "http://tracker.archiveteam.org/%s" % TRACKER_ID,
            downloader=downloader,
            version=VERSION,
            files=[
                ItemInterpolation("%(data_dir)s/%(warc_file_base)s.warc.gz")
                ],
            rsync_target_source_path=ItemInterpolation("%(data_dir)s/"),
            rsync_extra_args=[
                "--recursive",
                "--partial",
                "--partial-dir", ".rsync-tmp"
            ]
            ),
    ),
    SendDoneToTracker(
        tracker_url="http://%s/%s" % (TRACKER_HOST, TRACKER_ID),
        stats=ItemValue("stats")
    )
)
