"""Cursor-based pagination queries, mostly supporting condenser_api."""

from datetime import datetime
from dateutil.relativedelta import relativedelta

from hive.utils.normalize import rep_to_raw

# pylint: disable=too-many-lines

def last_month():
    """Get the date 1 month ago."""
    return datetime.now() + relativedelta(months=-1)

async def get_post_id(db, author, permlink):
    """Given an author/permlink, retrieve the id from db."""
    sql = ("SELECT id FROM hive_posts WHERE author = :a "
           "AND permlink = :p AND is_deleted = '0' LIMIT 1")
    return await db.query_one(sql, a=author, p=permlink)

async def get_child_ids(db, post_id):
    """Given a parent post id, retrieve all child ids."""
    sql = "SELECT id FROM hive_posts WHERE parent_id = :id AND is_deleted = '0'"
    return await db.query_col(sql, id=post_id)

async def _get_post_id(db, author, permlink):
    """Get post_id from hive db."""
    sql = "SELECT id FROM hive_posts WHERE author = :a AND permlink = :p"
    return await db.query_one(sql, a=author, p=permlink)

async def _get_account_id(db, name):
    """Get account id from hive db."""
    assert name, 'no account name specified'
    _id = await db.query_one("SELECT id FROM hive_accounts WHERE name = :n", n=name)
    assert _id, "account not found: `%s`" % name
    return _id


async def get_followers(db, account: str, start: str, follow_type: str, limit: int):
    """Get a list of accounts following a given account."""
    account_id = await _get_account_id(db, account)
    start_id = await _get_account_id(db, start) if start else None
    state = (2,3) if follow_type == 'ignore' else (1,3)

    seek = ''
    if start_id:
        seek = """AND hf.created_at <= (
                     SELECT created_at FROM hive_follows
                      WHERE following = :account_id
                        AND follower = :start_id)"""

    sql = """
        SELECT name,reputation,state FROM hive_follows hf
     LEFT JOIN hive_accounts ON hf.follower = id
         WHERE hf.following = :account_id
           AND state IN :state %s
      ORDER BY hf.created_at DESC
         LIMIT :limit
    """ % seek

    cache_key = "get_followers_" + str(account_id) + "_" + str(start_id) + "_" + str(state)

    return await db.query_all_cache(sql, cache_key, account_id=account_id, start_id=start_id,
                              state=state, limit=limit)


async def get_followers_by_page(db, account: str, page: int, page_size: int, follow_type: str):
    """Get a list of accounts following a given account."""
    account_id = await _get_account_id(db, account)
    state = (2,3) if follow_type == 'ignore' else (1,3)

    sql = """
        SELECT name,reputation,state FROM hive_follows hf
     LEFT JOIN hive_accounts ON hf.follower = id
         WHERE hf.following = :account_id
           AND state IN :state
      ORDER BY hf.created_at DESC
         LIMIT :limit OFFSET :offset
    """

    cache_key = "get_followers_by_page_" + str(account_id) + "_" + str(state) + "_" + str(page*page_size)

    return await db.query_all_cache(sql, cache_key, account_id=account_id,
                              state=state, limit=page_size, offset=page*page_size)

async def get_following(db, account: str, start: str, follow_type: str, limit: int):
    """Get a list of accounts followed by a given account."""
    account_id = await _get_account_id(db, account)
    start_id = await _get_account_id(db, start) if start else None
    state = (2, 3) if follow_type == 'ignore' else (1, 3)

    seek = ''
    if start_id:
        seek = """AND hf.created_at <= (
                     SELECT created_at FROM hive_follows
                      WHERE follower = :account_id
                        AND following = :start_id)"""

    sql = """
        SELECT name,reputation,state FROM hive_follows hf
     LEFT JOIN hive_accounts ON hf.following = id
         WHERE hf.follower = :account_id
           AND state IN :state %s
      ORDER BY hf.created_at DESC
         LIMIT :limit
    """ % seek

    cache_key = "get_following_" + str(account_id) + "_" + str(start_id) + "_" + str(state)

    return await db.query_all_cache(sql, cache_key, account_id=account_id, start_id=start_id,
                              state=state, limit=limit)


async def get_following_by_page(db, account: str, page: int, page_size: int, follow_type: str):
    """Get a list of accounts followed by a given account."""
    account_id = await _get_account_id(db, account)
    state = (2, 3) if follow_type == 'ignore' else (1, 3)

    sql = """
        SELECT name,reputation,state FROM hive_follows hf
     LEFT JOIN hive_accounts ON hf.following = id
         WHERE hf.follower = :account_id
           AND state IN :state
      ORDER BY hf.created_at DESC
         LIMIT :limit OFFSET :offset
    """

    cache_key = "get_following_by_page_" + str(account_id) + "_" + str(state) + "_" + str(page*page_size)

    return await db.query_all_cache(sql, cache_key, account_id=account_id,
                              state=state, limit=page_size, offset=page*page_size)


async def get_follow_counts(db, account: str):
    """Return following/followers count for `account`."""
    account_id = await _get_account_id(db, account)
    sql = """SELECT following, followers
               FROM hive_accounts
              WHERE id = :account_id"""
    return dict(await db.query_row(sql, account_id=account_id))


async def get_reblogged_by(db, author: str, permlink: str):
    """Return all rebloggers of a post."""
    post_id = await _get_post_id(db, author, permlink)
    assert post_id, "post not found"
    sql = """SELECT name FROM hive_accounts
               JOIN hive_feed_cache ON id = account_id
              WHERE post_id = :post_id"""
    names = await db.query_col(sql, post_id=post_id)
    names.remove(author)
    return names


async def get_account_reputations(db, account_lower_bound, limit):
    """Enumerate account reputations."""
    seek = ''
    if account_lower_bound:
        seek = "WHERE name >= :start"

    sql = """SELECT name, reputation
               FROM hive_accounts %s
           ORDER BY name
              LIMIT :limit""" % seek
    rows = await db.query_all(sql, start=account_lower_bound, limit=limit)
    return [dict(name=r[0], reputation=rep_to_raw(r[1])) for r in rows]


async def pids_by_query(db, sort, start_author, start_permlink, limit, tag):
    """Get a list of post_ids for a given posts query.

    `sort` can be trending, hot, created, promoted, payout, or payout_comments.
    """
    # pylint: disable=too-many-arguments,bad-whitespace,line-too-long
    assert sort in ['trending', 'hot', 'created', 'promoted',
                    'payout', 'payout_comments']

    params = {             # field      pending posts   comment promoted    todo        community
        'trending':        ('sc_trend', True,   False,  False,  False),   # posts=True  pending=False
        'hot':             ('sc_hot',   True,   False,  False,  False),   # posts=True  pending=False
        'created':         ('post_id',  False,  True,   False,  False),
        'promoted':        ('promoted', True,   False,  False,  True),    # posts=True
        'payout':          ('payout',   True,   True,   False,  False),
        'payout_comments': ('payout',   True,   False,  True,   False),
    }[sort]

    table = 'hive_posts_cache'
    field = params[0]
    where = []

    # primary filters
    if params[1]: where.append("is_paidout = '0'")
    if params[2]: where.append('depth = 0')
    if params[3]: where.append('depth > 0')
    if params[4]: where.append('promoted > 0')

    # filter by community, category, or tag
    if tag:
        #if tag[:5] == 'hive-'
        #    cid = get_community_id(tag)
        #    where.append('community_id = :cid')
        if sort in ['payout', 'payout_comments']:
            where.append('category = :tag')
        else:
            if tag[:5] == 'hive-':
                where.append('category = :tag')
                if sort in ('trending', 'hot'):
                    where.append('depth = 0')
            sql = "SELECT post_id FROM hive_post_tags WHERE tag = :tag"
            where.append("post_id IN (%s)" % sql)

    start_id = None
    if start_permlink:
        start_id = await _get_post_id(db, start_author, start_permlink)
        if not start_id:
            return []

        sql = "%s <= (SELECT %s FROM %s WHERE post_id = :start_id)"
        where.append(sql % (field, field, table))

    sql = ("SELECT post_id FROM %s WHERE %s ORDER BY %s DESC LIMIT :limit"
           % (table, ' AND '.join(where), field))

    return await db.query_col(sql, tag=tag, start_id=start_id, limit=limit)


async def pids_by_blog(db, account: str, start_author: str = '',
                       start_permlink: str = '', limit: int = 20):
    """Get a list of post_ids for an author's blog."""
    account_id = await _get_account_id(db, account)

    seek = ''
    start_id = None
    if start_permlink:
        start_id = await _get_post_id(db, start_author, start_permlink)
        if not start_id:
            return []

        seek = """
          AND created_at <= (
            SELECT created_at
              FROM hive_feed_cache
             WHERE account_id = :account_id
               AND post_id = :start_id)
        """

    sql = """
        SELECT post_id
          FROM hive_feed_cache
         WHERE account_id = :account_id %s
      ORDER BY created_at DESC
         LIMIT :limit
    """ % seek

    return await db.query_col(sql, account_id=account_id, start_id=start_id, limit=limit)


async def pids_by_blog_by_index(db, account: str, start_index: int, limit: int = 20):
    """Get post_ids for an author's blog (w/ reblogs), paged by index/limit.

    Examples:
    (acct, 2) = returns blog entries 0 up to 2 (3 oldest)
    (acct, 0) = returns all blog entries (limit 0 means return all?)
    (acct, 2, 1) = returns 1 post starting at idx 2
    (acct, 2, 3) = returns 3 posts: idxs (2,1,0)
    """

    account_id = await _get_account_id(db, account)

    if start_index in (-1, 0):
        sql = """SELECT COUNT(*) - 1 FROM hive_feed_cache
                  WHERE account_id = :account_id"""
        start_index = await db.query_one(sql, account_id=account_id)
        if start_index < 0:
            return (0, [])

    offset = start_index - limit + 1
    assert offset >= 0, ('start_index and limit combination is invalid (%d, %d)'
                         % (start_index, limit))

    sql = """
        SELECT post_id
          FROM hive_feed_cache
         WHERE account_id = :account_id
      ORDER BY created_at
         LIMIT :limit
        OFFSET :offset
    """

    ids = await db.query_col(sql, account_id=account_id, limit=limit, offset=offset)
    return (start_index, list(reversed(ids)))


async def pids_by_blog_without_reblog(db, account: str, start_permlink: str = '', limit: int = 20):
    """Get a list of post_ids for an author's blog without reblogs."""

    seek = ''
    start_id = None
    if start_permlink:
        start_id = await _get_post_id(db, account, start_permlink)
        if not start_id:
            return []
        seek = "AND id <= :start_id"

    sql = """
        SELECT id
          FROM hive_posts
         WHERE author = :account %s
           AND is_deleted = '0'
           AND depth = 0
      ORDER BY id DESC
         LIMIT :limit
    """ % seek

    return await db.query_col(sql, account=account, start_id=start_id, limit=limit)


async def pids_by_feed_with_reblog(db, account: str, start_author: str = '',
                                   start_permlink: str = '', limit: int = 20):
    """Get a list of [post_id, reblogged_by_str] for an account's feed."""
    account_id = await _get_account_id(db, account)

    seek = ''
    start_id = None
    if start_permlink:
        start_id = await _get_post_id(db, start_author, start_permlink)
        if not start_id:
            return []

        seek = """
          HAVING MIN(hive_feed_cache.created_at) <= (
            SELECT MIN(created_at) FROM hive_feed_cache WHERE post_id = :start_id
               AND account_id IN (SELECT following FROM hive_follows
                                  WHERE follower = :account AND state IN (1,3)))
        """

    sql = """
        SELECT post_id, string_agg(name, ',') accounts
          FROM hive_feed_cache
          JOIN hive_follows ON account_id = hive_follows.following AND state IN (1,3)
          JOIN hive_accounts ON hive_follows.following = hive_accounts.id
         WHERE hive_follows.follower = :account
           AND hive_feed_cache.created_at > :cutoff
      GROUP BY post_id %s
      ORDER BY MIN(hive_feed_cache.created_at) DESC LIMIT :limit
    """ % seek

    result = await db.query_all(sql, account=account_id, start_id=start_id,
                                limit=limit, cutoff=last_month())
    return [(row[0], row[1]) for row in result]


async def pids_by_account_comments(db, account: str, start_permlink: str = '', limit: int = 20):
    """Get a list of post_ids representing comments by an author."""
    seek = ''
    start_id = None
    if start_permlink:
        start_id = await _get_post_id(db, account, start_permlink)
        if not start_id:
            return []

        seek = "AND id <= :start_id"

    # `depth` in ORDER BY is a no-op, but forces an ix3 index scan (see #189)
    sql = """
        SELECT id FROM hive_posts
         WHERE author = :account %s
           AND depth > 0
           AND is_deleted = '0'
      ORDER BY id DESC, depth
         LIMIT :limit
    """ % seek

    return await db.query_col(sql, account=account, start_id=start_id, limit=limit)


async def pids_by_replies_to_account(db, start_author: str, start_permlink: str = '',
                                     limit: int = 20):
    """Get a list of post_ids representing replies to an author.

    To get the first page of results, specify `start_author` as the
    account being replied to. For successive pages, provide the
    last loaded reply's author/permlink.
    """
    seek = ''
    start_id = None
    if start_permlink:
        sql = """
          SELECT parent.author,
                 child.id
            FROM hive_posts child
            JOIN hive_posts parent
              ON child.parent_id = parent.id
           WHERE child.author = :author
             AND child.permlink = :permlink
        """

        row = await db.query_row(sql, author=start_author, permlink=start_permlink)
        if not row:
            return []

        parent_account = row[0]
        start_id = row[1]
        seek = "AND id <= :start_id"
    else:
        parent_account = start_author

    sql = """
    SELECT id FROM hive_posts
    WHERE author = :parent
    AND is_deleted = '0'
    ORDER BY id DESC
    LIMIT 10000
    """
    
    cache_key = "hive_posts-" + parent_account + "-is_deleted_0"
    print("what_is_the_ids_cache_key:" + cache_key)
    id_res = await db.query_all_cache(sql, cache_key, parent=parent_account)
    print(id_res)
    if id_res == None or len(id_res) == 0:
        return None
    tmp_ids = []
    for el in id_res:
        tmp_ids.append(str(el[0]))
    ids = ",".join(tmp_ids)
    print("what_is_the_ids:" + ids)

    sql = """
       SELECT id FROM hive_posts
        WHERE parent_id IN (%s) %s
          AND is_deleted = '0'
     ORDER BY id DESC
        LIMIT :limit
    """ % (ids, seek)

    return await db.query_col(sql, parent=parent_account, start_id=start_id, limit=limit)
