#!/bin/bash


seed=1
env=point_mass_maze_reach_top_right
for offset in 1 3 5 10 20
do
    for seed in 1 2 3 4 
    do
	./pph_general_wrap.sh $offset $seed
    done
done
#for task_no_goal in point_mass_maze_reach_hard_no_goal
#do
#    for task in point_mass_maze_reach_custom_hard_room
#    do
#	for seed in 1 2 3 4 
#        do
#            ./pph_general_wrap.sh $task_no_goal $task $seed
#        done
#    done
#done
#for task_no_goal in point_mass_maze_reach_room_no_goal
#do
#    for task in point_mass_maze_reach_custom_goal_room
#    do
#	for seed in 1 2 3 4 
#        do
#            ./pph_general_wrap.sh $task_no_goal $task $seed
#        done
#    done
#done

#env=point_mass_maze_reach_hard2_no_goal_v1
#./finetune_wrap.sh $env $REPLAY_BUFFER_SIZE $NUM_TRAIN_FRAMES $seed $load_seed
#env=point_mass_maze_reach_hard2_no_goal_v2
#./finetune_wrap.sh $env $REPLAY_BUFFER_SIZE $NUM_TRAIN_FRAMES $seed $load_seed
#env=point_mass_maze_reach_room_no_goal_v1
#./finetune_wrap.sh $env $REPLAY_BUFFER_SIZE $NUM_TRAIN_FRAMES $seed $load_seed
#env=point_mass_maze_reach_room_no_goal_v2
#./finetune_wrap.sh $env $REPLAY_BUFFER_SIZE $NUM_TRAIN_FRAMES $seed $load_seed