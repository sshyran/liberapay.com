from __future__ import division

from importlib import import_module
import os
import threading
import time
import traceback

import gittip
import gittip.wireup
from gittip import canonize, configure_payments
from gittip.security import authentication, csrf, x_frame_options
from gittip.utils import cache_static, timer
from gittip.elsewhere import platforms_ordered

from aspen import log_dammit

# Wireup Algorithm
# ================

version_file = os.path.join(website.www_root, 'version.txt')
__version__ = open(version_file).read().strip()
website.version = os.environ['__VERSION__'] = __version__


website.renderer_default = "jinja2"
website.default_renderers_by_media_type['application/json'] = 'stdlib_format'

website.renderer_factories['jinja2'].Renderer.global_context = {
    'range': range,
    'unicode': unicode,
    'enumerate': enumerate,
    'len': len,
    'float': float,
    'type': type,
    'str': str
}


gittip.wireup.canonical()
website.db = gittip.wireup.db()
gittip.wireup.billing()
gittip.wireup.username_restrictions(website)
gittip.wireup.nanswers()
gittip.wireup.nmembers(website)
gittip.wireup.envvars(website)
tell_sentry = gittip.wireup.make_sentry_teller(website)

# ensure platform_classes is populated
for platform in platforms_ordered:
    import_module("gittip.elsewhere.%s" % platform)


# The homepage wants expensive queries. Let's periodically select into an
# intermediate table.

UPDATE_HOMEPAGE_EVERY = int(os.environ['UPDATE_HOMEPAGE_EVERY'])
def update_homepage_queries():
    from gittip import utils
    while 1:
        try:
            utils.update_global_stats(website)
            utils.update_homepage_queries_once(website.db)
            website.db.self_check()
        except:
            if tell_sentry:
                tell_sentry(None)
            else:
                tb = traceback.format_exc().strip()
                log_dammit(tb)
        time.sleep(UPDATE_HOMEPAGE_EVERY)

if UPDATE_HOMEPAGE_EVERY > 0:
    homepage_updater = threading.Thread(target=update_homepage_queries)
    homepage_updater.daemon = True
    homepage_updater.start()
else:
    from gittip import utils
    utils.update_global_stats(website)


# Server Algorithm
# ================

def up_minthreads(website):
    # https://github.com/gittip/www.gittip.com/issues/1098
    # Discovered the following API by inspecting in pdb and browsing source.
    # This requires network_engine.bind to have already been called.
    request_queue = website.network_engine.cheroot_server.requests
    request_queue.min = website.min_threads


def setup_busy_threads_logging(website):
    # https://github.com/gittip/www.gittip.com/issues/1572
    log_every = website.log_busy_threads_every
    if log_every == 0:
        return

    pool = website.network_engine.cheroot_server.requests
    def log_busy_threads():
        time.sleep(0.5)  # without this we get a single log message where all threads are busy
        while 1:

            # Use pool.min and not pool.max because of the semantics of these
            # inside of Cheroot. (Max is a hard limit used only when pool.grow
            # is called, and it's never called except when the pool starts up,
            # when it's called with pool.min.)

            nbusy_threads = pool.min - pool.idle
            print("sample#aspen.busy_threads={}".format(nbusy_threads))
            time.sleep(log_every)

    thread = threading.Thread(target=log_busy_threads)
    thread.daemon = True
    thread.start()


website.server_algorithm.insert_before('start', up_minthreads)
website.server_algorithm.insert_before('start', setup_busy_threads_logging)


# Website Algorithm
# =================

def add_stuff_to_context(request):
    from gittip.elsewhere import platform_classes

    request.context['username'] = None

    for platform, cls in platform_classes.items():
        request.context[platform] = cls


algorithm = website.algorithm
algorithm.functions = [ timer.start
                      , algorithm['parse_environ_into_request']
                      , algorithm['tack_website_onto_request']
                      , algorithm['raise_200_for_OPTIONS']

                      , canonize
                      , configure_payments
                      , authentication.inbound
                      , csrf.inbound
                      , add_stuff_to_context

                      , algorithm['dispatch_request_to_filesystem']
                      , algorithm['apply_typecasters_to_path']

                      , cache_static.inbound

                      , algorithm['get_response_for_socket']
                      , algorithm['get_resource_for_request']
                      , algorithm['get_response_for_resource']
                      , algorithm['get_response_for_exception']

                      , authentication.outbound
                      , csrf.outbound
                      , cache_static.outbound
                      , x_frame_options

                      , algorithm['log_traceback_for_5xx']
                      , algorithm['delegate_error_to_simplate']
                      , algorithm['log_traceback_for_exception']
                      , algorithm['log_result_of_request']

                      , timer.end
                       ]
