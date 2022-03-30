import kopf
import re
from kubernetes import client
import pprint
import base64


@kopf.on.delete('clustersecret.io', 'v1', 'clustersecrets')
def on_delete(spec, uid, body, name, logger=None, **_):
    syncedns = body['status']['create_fn']['syncedns']
    v1 = client.CoreV1Api()
    for ns in syncedns:
        logger.info(f'deleting secret {name} from namespace {ns}')
        try:
            v1.delete_namespaced_secret(name, ns)
        except client.rest.ApiException as e:
            if e.status == 404:
                logger.warning(f"The namespace {ns} may not exist anymore: Not found")
            else:
                logger.warning(f" Something wierd deleting the secret: {e}")

    # delete also from memory: prevent syncing with new namespaces
    try:
        csecs.pop(uid)
        logger.debug(f"csec {uid} deleted from memory ok")
    except KeyError as k:
        logger.info(f" This csec were not found in memory, maybe it was created in another run: {k}")


@kopf.on.field('clustersecret.io', 'v1', 'clustersecrets', field='data')
def on_field_data(old, new, body, name, logger=None, **_):
    logger.debug(f'Data changed: {old} -> {new}')
    if old is not None:
        syncedns = body['status']['create_fn']['syncedns']
        v1 = client.CoreV1Api()

        secret_type = 'kubernetes.io/dockercfg'
        if 'type' in body["spec"]:
            secret_type = body["spec"]['type']

        for ns in syncedns:
            logger.info(f'Re Syncing secret {name} in ns {ns}')
            data = {name: base64.b64encode(data.encode("ascii")).decode("utf-8")
                    for name, data in new.items()}
            body = client.V1Secret(metadata=client.V1ObjectMeta(name=name),
                                   data=data,
                                   type=secret_type)
            response = v1.replace_namespaced_secret(name, ns, body)
            logger.debug(response)
    else:
        logger.debug('This is a new object')


csecs = {}  # all cluster secrets.


@kopf.on.resume('clustersecret.io', 'v1', 'clustersecrets')
@kopf.on.create('clustersecret.io', 'v1', 'clustersecrets')
async def create_fn(spec, uid, logger=None, body=None, **kwargs):
    v1 = client.CoreV1Api()

    # get all ns matching.
    matchedns = get_ns_list(logger, body, v1)

    # sync in all matched NS
    logger.info(f'Syncing on Namespaces: {matchedns}')
    for namespace in matchedns:
        create_secret(logger, namespace, body, v1)

    # store status in memory
    csecs[uid] = {}
    csecs[uid]['body'] = body
    csecs[uid]['syncedns'] = matchedns

    return {'syncedns': matchedns}


def get_ns_list(logger, body, v1=None):
    """Returns a list of namespaces where the secret should be matched
    """
    if v1 is None:
        v1 = client.CoreV1Api()
        logger.debug('new client - fn get_ns_list')
    logger.debug("BODY:{}".format(pprint.pformat(body)))
    try:
        matchNamespace = body["spec"]['matchNamespace']
    except KeyError:
        matchNamespace = '*'
        logger.debug("matching all namespaces.")
    logger.debug(f'Matching namespaces: {matchNamespace}')

    try:
        avoidNamespaces = body["spec"]['avoidNamespaces']
    except KeyError:
        avoidNamespaces = ''
        logger.debug("not avoiding namespaces")

    nss = v1.list_namespace().items
    matchedns = []
    avoidedns = []

    for matchns in matchNamespace:
        for ns in nss:
            if re.match(matchns, ns.metadata.name):
                matchedns.append(ns.metadata.name)
                logger.debug(f'Matched namespaces: {ns.metadata.name} matchpathern: {matchns}')
    if avoidNamespaces:
        for avoidns in avoidNamespaces:
            for ns in nss:
                if re.match(avoidns, ns.metadata.name):
                    avoidedns.append(ns.metadata.name)
                    logger.debug(f'Skipping namespaces: {ns.metadata.name} avoidpatrn: {avoidns}')
                    # purge
    for ns in matchedns.copy():
        if ns in avoidedns:
            matchedns.remove(ns)

    return matchedns


def create_secret(logger, namespace, body, v1=None):
    """Creates a given secret on a given namespace
    """
    if v1 is None:
        v1 = client.CoreV1Api()
        logger.debug('new client - fn create secret')
    try:
        name = body['metadata']['name']
    except KeyError:
        logger.debug("No name in body ?")
        raise kopf.TemporaryError("can not get the name.")
    try:
        data = {name: base64.b64encode(data.encode("ascii")).decode("utf-8")
                for name, data in body['spec']['data'].items()}
    except KeyError:
        data = ''
        logger.error("Empty secret?? could not get the data.")

    secret_type = 'kubernetes.io/dockercfg'
    if 'type' in body:
        secret_type = body["spec"]['type']

    body = client.V1Secret(metadata=client.V1ObjectMeta(name=name),
                           data=data,
                           type=secret_type)
    logger.info(f"cloning secret {body} in namespace {namespace}")
    try:
        _ = v1.create_namespaced_secret(namespace, body)
    except client.rest.ApiException as e:
        if e.reason == 'Conflict':
            logger.warning(f"secret `{name}` already exist in namesace '{namespace}'")
            return 0
        logger.error(f'Can not create a secret, it is base64 encoded? data: {data}')
        logger.error(f'Kube exception {e}')
        return 1
    return 0


@kopf.on.create('', 'v1', 'namespaces')
async def namespace_watcher(patch, logger, meta, body, event, **kwargs):
    """Watch for namespace events
    """
    new_ns = meta['name']
    logger.debug(f"New namespace created: {new_ns} re-syncing")
    v1 = client.CoreV1Api()

    for k, v in csecs.items():
        obj_body = v['body']
        # logger.debug(f'k: {k} \n v:{v}')
        matcheddns = v['syncedns']
        logger.debug(f"Old matched namespace: {matcheddns} - name: {v['body']['metadata']['name']}")
        ns_new_list = get_ns_list(logger, obj_body, v1)
        logger.debug(f"new matched list: {ns_new_list}")
        if new_ns in ns_new_list:
            logger.debug(f"Clonning secret {v['body']['metadata']['name']} into the new namespace {new_ns}")
            create_secret(logger, new_ns, v['body'], v1)
            # if there is a new matching ns, refresh memory
            v['syncedns'] = ns_new_list

    # update ns_new_list on the object so then we also delete from there
    return {'syncedns': ns_new_list}
