from typing import TYPE_CHECKING

from sequence.resource_management.memory_manager import MemoryInfo
from sequence.entanglement_management.generation.generation_base import EntanglementGenerationA
from sequence.resource_management.action_condition_set import Arguments, ActionReturn, TempNode

if TYPE_CHECKING:
    from sequence.components.memory import Memory
    from sequence.entanglement_management.entanglement_protocol import EntanglementProtocol


def eg_rule_action_await_adaptive(memories_info: list[MemoryInfo], args: Arguments) -> ActionReturn:
    """Action function used by entanglement generation protocol on nodes except the initiator, i.e., index > 0
    """
    memories: list[Memory] = [info.memory for info in memories_info]
    memory = memories[0]
    mid = args["mid"]
    path = args["path"]
    index = args["index"]
    from_app_request = args["from_app_request"]
    protocol = EntanglementGenerationA.create(owner=TempNode, name=f"EGA.{memory.name}",
                                              middle=mid, other=path[index - 1], memory=memory,
                                              from_app_request=from_app_request)

    return protocol, [None], [None], [None]


def eg_rule_action_request_adaptive(memories_info: list[MemoryInfo], args: Arguments) -> ActionReturn:
    """Action function used by entanglement generation protocol on nodes except the responder, i.e., index < len(path) - 1
    """
    mid = args["mid"]
    path = args["path"]
    index = args["index"]
    memories = [info.memory for info in memories_info]
    memory = memories[0]
    from_app_request = args["from_app_request"]
    protocol = EntanglementGenerationA.create(owner=TempNode, name=f"EGA.{memory.name}",
                                              middle=mid, other=path[index + 1], memory=memory,
                                              from_app_request=from_app_request)
    req_args = {"name": args["name"], "reservation": args["reservation"]}
    return protocol, [path[index + 1]], [eg_match_func_adaptive], [req_args]


def eg_match_func_adaptive(protocols: list[EntanglementProtocol], args: Arguments) -> EntanglementGenerationA | None:
    """Function used by `eg_rule_action2` function for selecting generation protocols on the remote node
    Args:
        protocols: the waiting protocols (wait for request)
        args: arguments from the node who sent the request
    Return:
        the selected protocol (there could be multiple protocols that satisfy the condition, if so, return the first protocol)
    """
    name = args["name"]
    reservation = args["reservation"]
    for protocol in protocols:
        if (isinstance(protocol, EntanglementGenerationA)
                and protocol.remote_node_name == name
                and protocol.rule.get_reservation() == reservation):
            return protocol
    return None
