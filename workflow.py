import os
import shutil


from virtool_core.utils import compress_file, rm
from virtool_workflow import startup, step, hooks
from virtool_workflow.storage.paths import data_path, temp_path

import utils


@startup
async def clean_up(db, job_params, run_in_executor):
    """
    Clean up if the job process encounters an error or is cancelled. Removes the host document from the database
    and deletes any index files.

    """

    @hooks.on_cancelled
    @hooks.on_failure
    async def delete_subtraction():
        try:
            await run_in_executor(
                rm,
                job_params["subtraction_path"],
                True
            )
        except FileNotFoundError:
            pass

        await db.subtraction.delete_one({"_id": job_params["subtraction_id"]})

        return "delete_subtraction completed"


@startup
def check_db(job_params, data_path, temp_path):
    subtraction_id = job_params["subtraction_id"]
    file_id = job_params["file_id"]

    subtraction_path = os.path.join(
        data_path,
        "subtractions",
        subtraction_id.replace(" ", "_").lower()
    )

    temp_subtraction_path = os.path.join(
        temp_path,
        subtraction_id
    )

    job_params.update({
        "subtraction_path": subtraction_path,
        "temp_subtraction_path": temp_subtraction_path,

        # The path to the uploaded FASTA file to be used for creating a subtraction.
        "file_path": os.path.join(
            data_path,
            "files",
            file_id
        ),

        "temp_fasta_path": os.path.join(
            temp_subtraction_path,
            "subtraction.fa"
        ),

        "temp_index_path": os.path.join(
            temp_subtraction_path,
            "reference"
        )
    })

    return "check_db completed"


@step
async def make_subtraction_dir(job_params, run_in_executor):
    """
    Make a directory for the host index files at ``<temp_path>/<subtraction_id>``.

    """
    await run_in_executor(
        os.makedirs,
        job_params["temp_subtraction_path"],
        exist_ok=True
    )

    return "make_subtraction_dir completed"


@step
async def unpack(job_params, number_of_processes, run_in_executor):
    """
    Unpack the FASTA file if it is gzipped.

    """
    await run_in_executor(
        utils.copy_or_decompress,
        job_params["file_path"],
        job_params["temp_fasta_path"],
        number_of_processes
    )
    return "unpack completed"


@step
async def set_stats(job_params, db):
    """
    Generate some stats for the FASTA file associated with this job. These numbers include nucleotide distribution,
    length distribution, and sequence count.

    """
    gc, count = await utils.calculate_fasta_gc(job_params["temp_fasta_path"])
    await db.subtraction.update_one({"_id": job_params["subtraction_id"]}, {
        "$set": {
            "gc": gc,
            "count": count
        }
    })

    return "set_stats completed"


@step
async def bowtie_build(db, job_params, number_of_processes, run_subprocess):
    """
    Call *bowtie2-build* to build a Bowtie2 index for the host.

    """
    command = [
        "bowtie2-build",
        "-f",
        "--threads", str(number_of_processes),
        job_params["temp_fasta_path"],
        job_params["temp_index_path"]
    ]

    await run_subprocess(command)

    await db.subtraction.update_one({"_id": job_params["subtraction_id"]}, {
        "$set": {
            "ready": True
        }
    })

    return "bowtie_build completed"


@step
async def compress(job_params, number_of_processes, run_in_executor):
    """
    Compress the subtraction FASTA file for long-term storage and download.

    """
    await run_in_executor(
        compress_file,
        job_params["temp_fasta_path"],
        job_params["temp_fasta_path"] + ".gz",
        number_of_processes
    )

    await run_in_executor(
        rm,
        job_params["temp_fasta_path"]
    )

    await run_in_executor(
        shutil.copytree,
        job_params["temp_subtraction_path"],
        job_params["subtraction_path"]
    )

    return "compress completed"
