#!/usr/bin/env python3
import argparse
import subprocess
import logging
import shutil
import json


logging.basicConfig(level=logging.INFO)
log = logging.getLogger("vm-migration")


def parse_args():
    parser = argparse.ArgumentParser("migration")
    parser.add_argument(
        "--vm-user", help="user on vm with ssh access", required=True
    )  # noqa: E501
    parser.add_argument(
        "--vm-host", help="ip or hostname of vm", required=True
    )  # noqa: E501
    parser.add_argument("--db-host", help="db host", required=True)
    parser.add_argument("--db-root-user", help="db root user", required=True)
    parser.add_argument(
        "--db-root-password", help="db root password", required=True
    )  # noqa: E501
    parser.add_argument("--bench-dir", help="source bench dir", required=True)
    parser.add_argument(
        "--site", help="name of site to be copied", required=True
    )  # noqa: E501
    parser.add_argument("--dest-dir", help="site directory on destination")
    parser.add_argument(
        "--dest-bench",
        help="destination bench dir",
        default="/home/frappe/frappe-bench",
    )
    parser.add_argument("--keyfile-path", help="site directory on destination")
    parser.add_argument(
        "--dbaas",
        help="set if using DBaaS",
        action=argparse.BooleanOptionalAction,  # noqa: E501
    )
    return parser.parse_args()


def pause_bench(args: argparse.Namespace):
    keyfile_path = "/home/frappe/.ssh/id_rsa"
    ssh_cmd = f"cd {args.bench_dir};"
    ssh_cmd += f"bench --site {args.site} set-config --as-dict maintenance_mode 1;"  # noqa: E501
    ssh_cmd += (
        f"bench --site {args.site} set-config --as-dict pause_scheduler 1"  # noqa: E501
    )
    cmd = f'ssh -i {args.keyfile_path or keyfile_path} {args.vm_user}@{args.vm_host} "{ssh_cmd}"'  # noqa: E501

    out, err = subprocess.Popen(
        cmd,
        shell=True,
        stdout=subprocess.PIPE,
    ).communicate()

    if err:
        log.error(err)

    log.info(out)


def restore_database(args: argparse.Namespace):
    keyfile_path = "/home/frappe/.ssh/id_rsa"

    # Read remote site_config
    site_config_file = (
        args.bench_dir + "/sites/" + args.site + "/site_config.json"
    )  # noqa: E501

    ssh_cmd = f"cat {site_config_file}"
    cmd = f'ssh -i {args.keyfile_path or keyfile_path} {args.vm_user}@{args.vm_host} "{ssh_cmd}"'  # noqa: E501
    site_cfg_str = subprocess.check_output(cmd, shell=True)
    site_config = json.loads(site_cfg_str)

    # set db perms
    permissions = (
        (
            "SELECT, INSERT, UPDATE, DELETE, CREATE, DROP, INDEX, ALTER, "
            "CREATE TEMPORARY TABLES, CREATE VIEW, EVENT, TRIGGER, SHOW VIEW, "
            "CREATE ROUTINE, ALTER ROUTINE, EXECUTE, LOCK TABLES"
        )
        if args.dbaas
        else "ALL PRIVILEGES"
    )

    # create db_name and user db_name@% with db_password and grant priv
    db_name = site_config.get("db_name")
    db_password = site_config.get("db_password")
    mysql_query = f"CREATE DATABASE `{db_name}`;"
    mysql_query += f"UPDATE mysql.global_priv SET Host = '%' where User = '{db_name}';"  # noqa: E501
    mysql_query += (
        f"SET PASSWORD FOR '{db_name}'@'%' = PASSWORD('{db_password}');"  # noqa: E501
    )
    mysql_query += f"GRANT {permissions} ON `{db_name}`.* TO '{db_name}'@'%';FLUSH PRIVILEGES;"  # noqa: E501

    mysql_cmd = f"mysql -u{args.db_root_user} -p'{args.db_root_password}' -h{args.db_host} -e \"{mysql_query}\""  # noqa: E501
    res = subprocess.check_output(mysql_cmd, shell=True)
    log.info(res)

    # backup remote database
    ssh_cmd = f"cd {args.bench_dir};"
    ssh_cmd += f"bench --site {args.site} backup"
    cmd = f'ssh -i {args.keyfile_path or keyfile_path} {args.vm_user}@{args.vm_host} "{ssh_cmd}"'  # noqa: E501
    out, err = subprocess.Popen(
        cmd,
        shell=True,
        stdout=subprocess.PIPE,
    ).communicate()

    if err:
        log.error(err)

    log.info(out)

    # restore backup to db_host
    restore_file = ""
    ssh_cmd = f"gunzip < {restore_file} | mysql -u{args.db_root_user} -p'{args.db_root_password}' -h{args.db_host} {db_name}"  # noqa: E501
    cmd = f'ssh -i {args.keyfile_path or keyfile_path} {args.vm_user}@{args.vm_host} "{ssh_cmd}"'  # noqa: E501
    out, err = subprocess.Popen(
        cmd,
        shell=True,
        stdout=subprocess.PIPE,
    ).communicate()

    if err:
        log.error(err)

    log.info(out)


def rsync_files(args: argparse.Namespace):
    keyfile_path = "/home/frappe/.ssh/id_rsa"
    cmd = f'rsync -ave "ssh -i {args.keyfile_path or keyfile_path}" {args.vm_user}@{args.vm_host}:{args.bench_dir}/sites/{args.site} {args.dest_bench}/sites/'  # noqa: E501

    out, err = subprocess.Popen(
        cmd,
        shell=True,
        stdout=subprocess.PIPE,
    ).communicate()

    if err:
        log.error(err)

    log.info(out)


def move_site_to_dest_dir(args: argparse.Namespace):
    if args.dest_dir:
        shutil.move(
            f"{args.dest_bench}/sites/{args.site}",
            f"{args.dest_bench}/sites/{args.dest_dir}",
        )


def unpause_bench(args: argparse.Namespace):
    log.info("unpausing bench")

    site = args.site
    if args.dest_dir:
        site = args.dest_dir

    site_config = args.dest_bench + site + "/site_config.json"

    data = {}
    with open(site_config, "r") as json_file:
        data = json.load(json_file)

    data["maintenance_mode"] = 0
    data["pause_scheduler"] = 0

    with open(site_config, "w") as json_file:
        json.dump(data, json_file, indent=2)

    log.info("bench unpaused")


def main():
    args = parse_args()
    pause_bench(args)
    restore_database(args)
    rsync_files(args)
    move_site_to_dest_dir(args)
    unpause_bench(args)


if __name__ == "__main__":
    main()


# migratevm.py \
#  --dbaas \
#  --vm-user=ubuntu \
#  --vm-host=ec2-12-34-56-78.compute-1.amazonaws.com \
#  --db-host=database.random.us-east-1.rds.amazonaws.com \
#  --db-root-user=root \
#  --db-root-password="changeit" \
#  --bench-dir=/home/frappe/frappe-bench \
#  --site=erp.example.com \
#  --keyfile-path=/workspace/gitops/.ssh/id_rsa
